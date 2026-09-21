from __future__ import annotations

import heapq
import itertools
import json
import logging
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from inboxzero import db

log = logging.getLogger("inboxzero.jobs")

P_ACTION = 0
P_USER = 1
P_SCAN = 2
P_INDEX = 3

LANE_IMAP = "imap"
LANE_LLM = "llm"
LANES = (LANE_IMAP, LANE_LLM)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass(order=True)
class Job:
    priority: int
    seq: int
    id: int = field(compare=False)
    kind: str = field(compare=False)
    payload: dict[str, Any] = field(compare=False, default_factory=dict)
    lane: str = field(compare=False, default=LANE_IMAP)
    done: threading.Event = field(compare=False, default_factory=threading.Event)
    result: dict[str, Any] | None = field(compare=False, default=None)
    error: str | None = field(compare=False, default=None)
    status: str = field(compare=False, default="queued")
    progress: str = field(compare=False, default="")

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "progress": self.progress,
            "error": self.error,
            "priority": self.priority,
            "payload": {k: v for k, v in self.payload.items() if k in {"item_id", "item_ids", "n", "action_id", "manual"}},
            "result": self.result or {},
        }


class EventBus:
    """Fan-out of server events to SSE subscribers, with a short replay buffer."""

    def __init__(self, history: int = 200) -> None:
        self._lock = threading.Lock()
        self._subs: list[queue.Queue] = []
        self._seq = itertools.count(1)
        self.history: deque[dict[str, Any]] = deque(maxlen=history)

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=500)
        with self._lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)

    def publish(self, kind: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        event = {"id": next(self._seq), "type": kind, "at": now_iso(), "data": data or {}}
        with self._lock:
            self.history.append(event)
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(event)
            except queue.Full:
                pass
        return event

    def since(self, last_id: int) -> list[dict[str, Any]]:
        with self._lock:
            return [event for event in self.history if int(event["id"]) > last_id]

    @property
    def subscribers(self) -> int:
        with self._lock:
            return len(self._subs)


def sse_format(event: dict[str, Any]) -> str:
    body = json.dumps(event.get("data") or {}, ensure_ascii=False)
    return f"id: {event['id']}\nevent: {event['type']}\ndata: {body}\n\n"


class JobContext:
    def __init__(self, runner: "JobRunner", job: Job) -> None:
        self.runner = runner
        self.job = job
        self.started = time.monotonic()

    @property
    def payload(self) -> dict[str, Any]:
        return self.job.payload

    def progress(self, text: str) -> None:
        self.job.progress = text[:300]
        self.runner.persist_progress(self.job)
        self.runner.bus.publish("job", self.job.public())

    def should_yield(self, max_priority: int = P_USER, budget_s: float | None = None) -> bool:
        if budget_s is not None and time.monotonic() - self.started > budget_s:
            return True
        return self.runner.has_pending(max_priority, lane=self.job.lane)

    def elapsed(self) -> float:
        return time.monotonic() - self.started


Handler = Callable[[JobContext], dict[str, Any] | None]


class _Lane:
    def __init__(self, name: str) -> None:
        self.name = name
        self.heap: list[Job] = []
        self.cv = threading.Condition()
        self.thread: threading.Thread | None = None
        self.current: Job | None = None


class JobRunner:
    """Priority queues per lane, one worker thread each. Jobs are persisted for history."""

    def __init__(self, db_path: str, bus: EventBus | None = None, persist: bool = True) -> None:
        self.db_path = db_path
        self.bus = bus or EventBus()
        self.persist = persist
        self._handlers: dict[str, tuple[Handler, str]] = {}
        self._lanes = {name: _Lane(name) for name in LANES}
        self._seq = itertools.count()
        self._stop = threading.Event()
        self._jobs: dict[int, Job] = {}
        self._jobs_lock = threading.Lock()
        self._local_ids = itertools.count(-1, -1)

    # --- registration -------------------------------------------------------

    def register(self, kind: str, handler: Handler, lane: str = LANE_IMAP) -> None:
        if lane not in self._lanes:
            raise ValueError(lane)
        self._handlers[kind] = (handler, lane)

    def lane_of(self, kind: str) -> str:
        return self._handlers[kind][1]

    # --- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        self._stop.clear()
        for lane in self._lanes.values():
            if lane.thread and lane.thread.is_alive():
                continue
            lane.thread = threading.Thread(target=self._loop, args=(lane,), name=f"inboxzero-{lane.name}", daemon=True)
            lane.thread.start()

    def stop(self) -> None:
        self._stop.set()
        for lane in self._lanes.values():
            with lane.cv:
                lane.cv.notify_all()

    # --- submit -------------------------------------------------------------

    def _new_job_id(self, kind: str, payload: dict[str, Any], priority: int) -> int:
        if not self.persist:
            return next(self._local_ids)
        try:
            return db.job_create(self.db_path, kind, payload, priority)
        except Exception as exc:
            log.warning("job persist failed: %s", exc)
            return next(self._local_ids)

    def submit(
        self,
        kind: str,
        payload: dict[str, Any] | None = None,
        priority: int = P_USER,
        dedupe: bool = False,
    ) -> Job:
        if kind not in self._handlers:
            raise KeyError(f"job inconnu : {kind}")
        payload = dict(payload or {})
        lane = self._lanes[self._handlers[kind][1]]
        with lane.cv:
            if dedupe:
                for queued in lane.heap:
                    if queued.kind == kind and queued.payload == payload:
                        return queued
                if lane.current and lane.current.kind == kind and lane.current.payload == payload:
                    return lane.current
            job = Job(
                priority=int(priority),
                seq=next(self._seq),
                id=self._new_job_id(kind, payload, priority),
                kind=kind,
                payload=payload,
                lane=lane.name,
            )
            heapq.heappush(lane.heap, job)
            with self._jobs_lock:
                self._jobs[job.id] = job
            lane.cv.notify()
        self.bus.publish("job", job.public())
        return job

    def call(self, kind: str, payload: dict[str, Any] | None = None, priority: int = P_USER, timeout: float = 90) -> dict[str, Any]:
        job = self.submit(kind, payload, priority)
        if not job.done.wait(timeout):
            raise TimeoutError(f"{kind} : délai dépassé")
        if job.error:
            raise RuntimeError(job.error)
        return job.result or {}

    def run_inline(self, kind: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Execute a handler in the calling thread (tests, CLI)."""
        handler, lane_name = self._handlers[kind]
        job = Job(priority=P_USER, seq=next(self._seq), id=self._new_job_id(kind, payload or {}, P_USER), kind=kind, payload=dict(payload or {}), lane=lane_name)
        self._execute(job)
        if job.error:
            raise RuntimeError(job.error)
        return job.result or {}

    def drain(self, max_jobs: int = 50) -> int:
        """Run queued jobs synchronously (tests)."""
        ran = 0
        for lane in self._lanes.values():
            while lane.heap and ran < max_jobs:
                with lane.cv:
                    job = heapq.heappop(lane.heap)
                self._execute(job)
                ran += 1
        return ran

    # --- introspection -------------------------------------------------------

    def has_pending(self, max_priority: int, lane: str | None = None) -> bool:
        lanes = [self._lanes[lane]] if lane else list(self._lanes.values())
        for entry in lanes:
            with entry.cv:
                if any(job.priority <= max_priority for job in entry.heap):
                    return True
        return False

    def get(self, job_id: int) -> Job | None:
        with self._jobs_lock:
            return self._jobs.get(job_id)

    def snapshot(self) -> dict[str, Any]:
        out: dict[str, Any] = {"lanes": {}}
        for name, lane in self._lanes.items():
            with lane.cv:
                out["lanes"][name] = {
                    "current": lane.current.public() if lane.current else None,
                    "queued": [job.public() for job in sorted(lane.heap)[:10]],
                    "queued_count": len(lane.heap),
                }
        return out

    def persist_progress(self, job: Job) -> None:
        if not self.persist or job.id < 0:
            return
        try:
            db.job_update(self.db_path, job.id, progress=job.progress)
        except Exception:
            pass

    # --- execution -------------------------------------------------------------

    def _loop(self, lane: _Lane) -> None:
        while not self._stop.is_set():
            with lane.cv:
                while not lane.heap and not self._stop.is_set():
                    lane.cv.wait(timeout=5)
                if self._stop.is_set():
                    return
                job = heapq.heappop(lane.heap)
                lane.current = job
            try:
                self._execute(job)
            finally:
                with lane.cv:
                    lane.current = None
            if job.result and job.result.get("continue"):
                try:
                    self.submit(job.kind, job.payload, job.priority, dedupe=True)
                except Exception as exc:
                    log.warning("continuation failed: %s", exc)

    def _execute(self, job: Job) -> None:
        handler, _lane = self._handlers[job.kind]
        job.status = "running"
        self._persist(job, status="running")
        self.bus.publish("job", job.public())
        try:
            result = handler(JobContext(self, job)) or {}
            job.result = result
            job.status = "done"
            self._persist(job, status="done", result=result)
        except Exception as exc:
            job.error = str(exc)[:800] or exc.__class__.__name__
            job.status = "failed"
            log.warning("job %s #%s failed: %s", job.kind, job.id, job.error)
            self._persist(job, status="failed", error=job.error)
        finally:
            job.done.set()
            self.bus.publish("job", job.public())
            with self._jobs_lock:
                if len(self._jobs) > 400:
                    for key in [k for k, v in self._jobs.items() if v.done.is_set()][:200]:
                        self._jobs.pop(key, None)

    def _persist(self, job: Job, **fields: Any) -> None:
        if not self.persist or job.id < 0:
            return
        try:
            db.job_update(self.db_path, job.id, **fields)
        except Exception as exc:
            log.debug("job persist skipped: %s", exc)

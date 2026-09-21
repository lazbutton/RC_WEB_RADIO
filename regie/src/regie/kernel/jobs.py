from __future__ import annotations

import logging
import socket
import threading
import time
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Callable

from regie.kernel import db
from regie.kernel.events import EventBus

log = logging.getLogger("regie.jobs")

P_ACTION = 0
P_USER = 1
P_SCAN = 2
P_INDEX = 3
P_BACKGROUND = 4

LEASE_SECONDS = 120
BACKOFF_BASE = 15


@dataclass
class JobContext:
    runner: "JobRunner"
    row: dict[str, Any]
    started: float = field(default_factory=time.monotonic)

    @property
    def id(self) -> int:
        return int(self.row["id"])

    @property
    def payload(self) -> dict[str, Any]:
        return dict(self.row.get("payload") or {})

    @property
    def lane(self) -> str:
        return str(self.row["lane"])

    def progress(self, text: str) -> None:
        self.runner.progress(self.id, text)

    def should_yield(self, max_priority: int = P_USER, budget_s: float | None = None) -> bool:
        if budget_s is not None and time.monotonic() - self.started > budget_s:
            return True
        return self.runner.has_pending(self.lane, max_priority)

    def heartbeat(self) -> None:
        self.runner.extend_lease(self.id)


Handler = Callable[[JobContext], dict[str, Any] | None]


def public_job(row: dict[str, Any]) -> dict[str, Any]:
    payload = row.get("payload") or {}
    return {
        "id": int(row["id"]),
        "kind": row["kind"],
        "lane": row["lane"],
        "status": row["status"],
        "progress": row.get("progress") or "",
        "error": row.get("error") or None,
        "priority": int(row.get("priority") or 0),
        "attempts": int(row.get("attempts") or 0),
        "payload": {k: v for k, v in payload.items() if k in {"item_id", "item_ids", "n", "action_id", "manual", "entity_kind", "entity_id", "system"}},
        "result": row.get("result") or {},
        "created_at": db.iso(row.get("created_at")),
        "started_at": db.iso(row.get("started_at")),
        "finished_at": db.iso(row.get("finished_at")),
    }


class JobRunner:
    """File en Postgres, prise par bail (SKIP LOCKED), un ou plusieurs travailleurs par voie.

    Les travailleurs peuvent tourner dans un autre processus ou une autre machine : ils partagent la table.
    """

    def __init__(self, dsn: str, org_id: str, bus: EventBus, worker_name: str | None = None) -> None:
        self.dsn = dsn
        self.org_id = org_id
        self.bus = bus
        self.worker = worker_name or f"{socket.gethostname()}:{threading.get_ident()}"
        self._handlers: dict[str, tuple[Handler, str]] = {}
        self._lanes: dict[str, int] = {}
        self._threads: list[threading.Thread] = []
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._local_done: dict[int, threading.Event] = {}
        self._local_lock = threading.Lock()

    # --- déclaration -----------------------------------------------------------

    def register(self, kind: str, handler: Handler, lane: str, concurrency: int = 1) -> None:
        self._handlers[kind] = (handler, lane)
        self._lanes[lane] = max(self._lanes.get(lane, 1), concurrency)

    def lane_of(self, kind: str) -> str:
        return self._handlers[kind][1]

    def kinds(self) -> list[str]:
        return sorted(self._handlers)

    # --- soumission -------------------------------------------------------------

    def submit(
        self,
        kind: str,
        payload: dict[str, Any] | None = None,
        priority: int = P_USER,
        *,
        run_at: Any = None,
        idempotency_key: str | None = None,
        dedupe: bool = False,
        max_attempts: int = 5,
        created_by: str | None = None,
        lane: str | None = None,
    ) -> dict[str, Any]:
        payload = dict(payload or {})
        lane = lane or (self._handlers[kind][1] if kind in self._handlers else None)
        if lane is None:
            raise KeyError(f"job inconnu : {kind}")
        if dedupe and not idempotency_key:
            idempotency_key = f"{kind}:{db.dumps(payload)}"
        with db.connect(self.dsn) as conn:
            if idempotency_key:
                existing = db.fetch_one(
                    conn,
                    "SELECT * FROM jobs WHERE idempotency_key = %s AND status IN ('queued', 'running') ORDER BY id DESC LIMIT 1",
                    (idempotency_key,),
                )
                if existing:
                    return public_job(existing)
            row = db.fetch_one(
                conn,
                """
                INSERT INTO jobs (org_id, lane, kind, priority, payload, idempotency_key, run_at, max_attempts, created_by)
                VALUES (%s, %s, %s, %s, %s, %s, COALESCE(%s, now()), %s, %s)
                RETURNING *
                """,
                (self.org_id, lane, kind, int(priority), db.J(payload), idempotency_key, run_at, max_attempts, created_by),
            )
        assert row is not None
        self._wake.set()
        job = public_job(row)
        self.bus.publish("job", job)
        return job

    def call(self, kind: str, payload: dict[str, Any] | None = None, priority: int = P_USER, timeout: float = 90) -> dict[str, Any]:
        """Soumet et attend le résultat (pièces jointes, tests)."""
        job = self.submit(kind, payload, priority)
        job_id = int(job["id"])
        if job["status"] in {"done", "failed", "dead"}:
            return self._finish_or_raise(self.get(job_id) or job)
        event = threading.Event()
        with self._local_lock:
            self._local_done[job_id] = event
        try:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if event.wait(0.5):
                    break
                current = self.get(job_id)
                if current and current["status"] in {"done", "failed", "dead", "cancelled"}:
                    break
            else:
                raise TimeoutError(f"{kind} : délai dépassé")
        finally:
            with self._local_lock:
                self._local_done.pop(job_id, None)
        return self._finish_or_raise(self.get(job_id) or job)

    def _finish_or_raise(self, job: dict[str, Any]) -> dict[str, Any]:
        if job["status"] in {"failed", "dead"}:
            raise RuntimeError(job.get("error") or "job en échec")
        return job.get("result") or {}

    def run_inline(self, kind: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Exécute un handler dans le thread courant (tests, CLI). Persisté comme les autres."""
        job = self.submit(kind, payload, P_USER)
        row = self._claim_specific(int(job["id"]))
        if row is None:
            raise RuntimeError("job déjà pris")
        self._execute(row)
        done = self.get(int(job["id"])) or {}
        return self._finish_or_raise(done)

    def drain(self, max_jobs: int = 100) -> int:
        """Exécute tout ce qui est prêt, synchrone (tests)."""
        ran = 0
        for _ in range(max_jobs):
            row = None
            for lane in sorted(self._lanes or {h[1] for h in self._handlers.values()}):
                row = self.claim(lane)
                if row:
                    break
            if not row:
                break
            self._execute(row)
            ran += 1
        return ran

    # --- lecture ------------------------------------------------------------------

    def get(self, job_id: int) -> dict[str, Any] | None:
        with db.connect(self.dsn) as conn:
            row = db.fetch_one(conn, "SELECT * FROM jobs WHERE id = %s", (job_id,))
        return public_job(row) if row else None

    def recent(self, limit: int = 30, kinds: list[str] | None = None) -> list[dict[str, Any]]:
        with db.connect(self.dsn) as conn:
            if kinds:
                rows = db.fetch_all(conn, "SELECT * FROM jobs WHERE kind = ANY(%s) ORDER BY id DESC LIMIT %s", (kinds, limit))
            else:
                rows = db.fetch_all(conn, "SELECT * FROM jobs ORDER BY id DESC LIMIT %s", (limit,))
        return [public_job(row) for row in rows]

    def has_pending(self, lane: str, max_priority: int) -> bool:
        with db.connect(self.dsn) as conn:
            return bool(
                db.scalar(
                    conn,
                    "SELECT 1 FROM jobs WHERE lane = %s AND status = 'queued' AND priority <= %s AND run_at <= now() LIMIT 1",
                    (lane, max_priority),
                )
            )

    def item_job(self, entity_id: Any, kind: str) -> dict[str, Any] | None:
        with db.connect(self.dsn) as conn:
            row = db.fetch_one(
                conn,
                "SELECT * FROM jobs WHERE kind = %s AND status IN ('queued', 'running') AND payload->>'item_id' = %s ORDER BY id DESC LIMIT 1",
                (kind, str(entity_id)),
            )
        return public_job(row) if row else None

    def snapshot(self) -> dict[str, Any]:
        with db.connect(self.dsn) as conn:
            rows = db.fetch_all(
                conn,
                """
                SELECT lane, status, COUNT(*) AS n FROM jobs
                WHERE status IN ('queued', 'running') OR finished_at > now() - interval '1 hour'
                GROUP BY lane, status
                """,
            )
            running = db.fetch_all(conn, "SELECT * FROM jobs WHERE status = 'running' ORDER BY started_at")
            late = db.scalar(conn, "SELECT COUNT(*) FROM jobs WHERE status = 'queued' AND run_at < now() - interval '10 minutes'")
            dead = db.scalar(conn, "SELECT COUNT(*) FROM jobs WHERE status = 'dead'")
        lanes: dict[str, dict[str, int]] = {}
        for row in rows:
            lanes.setdefault(row["lane"], {})[row["status"]] = int(row["n"])
        return {"lanes": lanes, "running": [public_job(r) for r in running], "late": int(late or 0), "dead": int(dead or 0), "worker": self.worker}

    # --- bail et exécution ------------------------------------------------------------

    def claim(self, lane: str) -> dict[str, Any] | None:
        with db.connect(self.dsn) as conn:
            row = db.fetch_one(
                conn,
                """
                WITH picked AS (
                  SELECT id FROM jobs
                  WHERE lane = %s AND run_at <= now()
                    AND (status = 'queued' OR (status = 'running' AND lease_until < now()))
                  ORDER BY priority, run_at, id
                  FOR UPDATE SKIP LOCKED
                  LIMIT 1
                )
                UPDATE jobs SET status = 'running', worker = %s, lease_until = now() + make_interval(secs => %s),
                       started_at = COALESCE(started_at, now()), attempts = attempts + 1
                WHERE id IN (SELECT id FROM picked)
                RETURNING *
                """,
                (lane, self.worker, LEASE_SECONDS),
            )
        return row

    def _claim_specific(self, job_id: int) -> dict[str, Any] | None:
        with db.connect(self.dsn) as conn:
            return db.fetch_one(
                conn,
                """
                UPDATE jobs SET status = 'running', worker = %s, lease_until = now() + make_interval(secs => %s),
                       started_at = COALESCE(started_at, now()), attempts = attempts + 1
                WHERE id = %s AND status = 'queued'
                RETURNING *
                """,
                (self.worker, LEASE_SECONDS, job_id),
            )

    def extend_lease(self, job_id: int) -> None:
        with db.connect(self.dsn) as conn:
            db.execute(conn, "UPDATE jobs SET lease_until = now() + make_interval(secs => %s) WHERE id = %s", (LEASE_SECONDS, job_id))

    def progress(self, job_id: int, text: str) -> None:
        with db.connect(self.dsn) as conn:
            row = db.fetch_one(
                conn,
                "UPDATE jobs SET progress = %s, lease_until = now() + make_interval(secs => %s) WHERE id = %s RETURNING *",
                (text[:300], LEASE_SECONDS, job_id),
            )
        if row:
            self.bus.publish("job", public_job(row))

    def _execute(self, row: dict[str, Any]) -> None:
        kind = str(row["kind"])
        handler = self._handlers.get(kind)
        job_id = int(row["id"])
        self.bus.publish("job", public_job(row))
        if not handler:
            self._fail(job_id, f"aucun travailleur pour {kind}", final=True)
            return
        try:
            result = handler[0](JobContext(self, row)) or {}
            with db.connect(self.dsn) as conn:
                done = db.fetch_one(
                    conn,
                    "UPDATE jobs SET status = 'done', result = %s, error = '', finished_at = now(), lease_until = NULL WHERE id = %s RETURNING *",
                    (db.J(result), job_id),
                )
            if done:
                self.bus.publish("job", public_job(done))
            if result.get("continue"):
                self.submit(kind, dict(row.get("payload") or {}), int(row.get("priority") or P_SCAN), dedupe=True)
        except Exception as exc:
            attempts = int(row.get("attempts") or 1)
            final = attempts >= int(row.get("max_attempts") or 5)
            log.warning("job %s #%s échec %s/%s : %s", kind, job_id, attempts, row.get("max_attempts"), exc)
            self._fail(job_id, str(exc)[:800] or exc.__class__.__name__, final=final, attempts=attempts)
        finally:
            with self._local_lock:
                event = self._local_done.get(job_id)
            if event:
                event.set()

    def _fail(self, job_id: int, error: str, *, final: bool, attempts: int = 1) -> None:
        with db.connect(self.dsn) as conn:
            if final:
                row = db.fetch_one(
                    conn,
                    "UPDATE jobs SET status = 'dead', error = %s, finished_at = now(), lease_until = NULL WHERE id = %s RETURNING *",
                    (error, job_id),
                )
            else:
                delay = min(3600, BACKOFF_BASE * (2 ** max(0, attempts - 1)))
                row = db.fetch_one(
                    conn,
                    "UPDATE jobs SET status = 'queued', error = %s, run_at = now() + make_interval(secs => %s), lease_until = NULL WHERE id = %s RETURNING *",
                    (error, delay, job_id),
                )
        if row:
            self.bus.publish("job", public_job(row))

    def cancel(self, job_id: int) -> bool:
        with db.connect(self.dsn) as conn:
            return db.execute(conn, "UPDATE jobs SET status = 'cancelled', finished_at = now() WHERE id = %s AND status = 'queued'", (job_id,)) > 0

    def retry(self, job_id: int) -> bool:
        import psycopg.errors

        try:
            with db.connect(self.dsn) as conn:
                return db.execute(conn, "UPDATE jobs SET status = 'queued', attempts = 0, run_at = now(), error = '' WHERE id = %s AND status IN ('dead', 'failed', 'cancelled')", (job_id,)) > 0
        except psycopg.errors.UniqueViolation:
            # un job identique est déjà en file : celui-ci est simplement classé
            with db.connect(self.dsn) as conn:
                db.execute(conn, "UPDATE jobs SET status = 'cancelled', error = 'doublon : déjà en file', finished_at = now() WHERE id = %s", (job_id,))
            return False

    def prune(self, keep: int = 2000) -> int:
        with db.connect(self.dsn) as conn:
            return db.execute(
                conn,
                "DELETE FROM jobs WHERE status IN ('done', 'cancelled') AND id NOT IN (SELECT id FROM jobs ORDER BY id DESC LIMIT %s)",
                (keep,),
            )

    # --- travailleurs -------------------------------------------------------------------

    def start(self) -> None:
        self._stop.clear()
        lanes = self._lanes or {lane for _, lane in self._handlers.values()}
        for lane in lanes:
            count = self._lanes.get(lane, 1)
            for index in range(count):
                thread = threading.Thread(target=self._loop, args=(lane,), name=f"regie-job-{lane}-{index}", daemon=True)
                thread.start()
                self._threads.append(thread)

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def _loop(self, lane: str) -> None:
        while not self._stop.is_set():
            try:
                row = self.claim(lane)
                if row is None:
                    self._wake.wait(1.5)
                    self._wake.clear()
                    continue
                self._execute(row)
            except Exception as exc:
                log.warning("boucle %s : %s", lane, exc)
                self._stop.wait(2.0)


class Scheduler:
    """Planification récurrente : une ligne `schedules` par tâche de fond."""

    def __init__(self, dsn: str, org_id: str, runner: JobRunner) -> None:
        self.dsn = dsn
        self.org_id = org_id
        self.runner = runner
        self._stop = threading.Event()
        self.thread: threading.Thread | None = None

    def ensure(self, kind: str, every_seconds: int, payload: dict[str, Any] | None = None, priority: int = P_SCAN, lane: str | None = None, enabled: bool = True) -> None:
        lane = lane or self.runner.lane_of(kind)
        with db.connect(self.dsn) as conn:
            db.execute(
                conn,
                """
                INSERT INTO schedules (org_id, kind, lane, payload, every_seconds, priority, enabled)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (kind, payload) DO UPDATE SET every_seconds = EXCLUDED.every_seconds, lane = EXCLUDED.lane, priority = EXCLUDED.priority
                """,
                (self.org_id, kind, lane, db.J(payload or {}), int(every_seconds), int(priority), enabled),
            )

    def tick(self) -> int:
        fired = 0
        with db.connect(self.dsn) as conn:
            due = db.fetch_all(conn, "SELECT * FROM schedules WHERE enabled AND next_run_at <= now() ORDER BY next_run_at FOR UPDATE SKIP LOCKED")
            for row in due:
                db.execute(
                    conn,
                    "UPDATE schedules SET last_run_at = now(), next_run_at = now() + make_interval(secs => every_seconds) WHERE id = %s",
                    (row["id"],),
                )
        for row in due:
            try:
                self.runner.submit(str(row["kind"]), dict(row["payload"] or {}), int(row["priority"]), dedupe=True, lane=str(row["lane"]))
                fired += 1
            except Exception as exc:
                log.warning("planification %s : %s", row["kind"], exc)
        return fired

    def start(self) -> None:
        self._stop.clear()
        self.thread = threading.Thread(target=self._loop, name="regie-scheduler", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as exc:
                log.warning("scheduler : %s", exc)
            self._stop.wait(5)

    def list(self) -> list[dict[str, Any]]:
        with db.connect(self.dsn) as conn:
            return [db.jsonable(row) or {} for row in db.fetch_all(conn, "SELECT * FROM schedules ORDER BY kind")]


def timedelta_seconds(value: timedelta) -> int:
    return int(value.total_seconds())

from __future__ import annotations

import logging
import threading
from typing import Any, Callable

from regie.kernel import db
from regie.kernel.events import EventBus

log = logging.getLogger("regie.outbox")
Handler = Callable[[dict[str, Any]], None]


class Outbox:
    """Événements métier écrits dans la même transaction que la donnée, livrés par un relais.

    `emit()` s'appelle *dans* un `db.connect()` ouvert : la ligne outbox part avec le commit.
    Le relais livre au bus SSE et aux abonnés (notifications, réindexation, connecteurs sortants).
    """

    def __init__(self, dsn: str, org_id: str, bus: EventBus) -> None:
        self.dsn = dsn
        self.org_id = org_id
        self.bus = bus
        self._handlers: dict[str, list[Handler]] = {}
        self._stop = threading.Event()
        self._wake = threading.Event()
        self.thread: threading.Thread | None = None

    def on(self, topic: str, handler: Handler) -> None:
        """topic exact ou préfixe terminé par '*' (ex. 'mail.*')."""
        self._handlers.setdefault(topic, []).append(handler)

    def emit(self, topic: str, payload: dict[str, Any] | None = None) -> int:
        with db.connect(self.dsn) as conn:
            row = db.fetch_one(
                conn,
                "INSERT INTO outbox (org_id, topic, payload) VALUES (%s, %s, %s) RETURNING id",
                (self.org_id, topic, db.J(payload or {})),
            )
        self._wake.set()
        return int(row["id"]) if row else 0

    def _matching(self, topic: str) -> list[Handler]:
        out: list[Handler] = []
        for key, handlers in self._handlers.items():
            if key == topic or (key.endswith("*") and topic.startswith(key[:-1])):
                out.extend(handlers)
        return out

    def deliver_pending(self, limit: int = 200) -> int:
        with db.connect(self.dsn) as conn:
            rows = db.fetch_all(conn, "SELECT id, topic, payload FROM outbox WHERE delivered_at IS NULL ORDER BY id LIMIT %s", (limit,))
        delivered = 0
        for row in rows:
            topic = str(row["topic"])
            payload = dict(row["payload"] or {})
            error = ""
            try:
                self.bus.publish(topic, payload)
                for handler in self._matching(topic):
                    handler({"topic": topic, **payload})
            except Exception as exc:  # un abonné qui casse ne bloque pas la file
                error = str(exc)[:400]
                log.warning("outbox %s : %s", topic, exc)
            with db.connect(self.dsn) as conn:
                db.execute(
                    conn,
                    "UPDATE outbox SET delivered_at = now(), attempts = attempts + 1, error = %s WHERE id = %s",
                    (error, row["id"]),
                )
            delivered += 1
        return delivered

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self._stop.clear()
        self.thread = threading.Thread(target=self._loop, name="regie-outbox", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                if self.deliver_pending() == 0:
                    self._wake.wait(2.0)
                self._wake.clear()
            except Exception as exc:
                log.warning("relais outbox : %s", exc)
                self._stop.wait(2.0)

    def prune(self, keep_days: int = 14) -> int:
        with db.connect(self.dsn) as conn:
            return db.execute(conn, "DELETE FROM outbox WHERE delivered_at IS NOT NULL AND delivered_at < now() - make_interval(days => %s)", (keep_days,))

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from regie.kernel import db

log = logging.getLogger("regie.connectors")

PAUSE_AFTER_FAILURES = 5
PAUSE_SECONDS = 600


@dataclass
class Change:
    """Un changement lu chez un système externe."""

    external_id: str
    kind: str  # created | updated | deleted
    data: dict[str, Any] = field(default_factory=dict)
    etag: str = ""


@dataclass
class PullResult:
    changes: list[Change]
    cursor: dict[str, Any]
    more: bool = False


class Connector:
    """Interface commune : pull / push / map / health. Chaque connecteur a un faux jumeau pour les tests."""

    system: str = "abstract"
    label: str = "Connecteur"
    read_only: bool = False

    def configured(self) -> bool:
        return True

    def pull(self, cursor: dict[str, Any]) -> PullResult:
        raise NotImplementedError

    def push(self, entity_kind: str, entity_id: str, data: dict[str, Any], external_id: str | None) -> tuple[str, str]:
        """Écrit chez l'externe. Renvoie (external_id, etag). Doit être idempotent."""
        raise NotImplementedError

    def delete(self, external_id: str) -> None:
        raise NotImplementedError

    def health(self) -> dict[str, Any]:
        return {"ok": True}


class ExternalRefs:
    def __init__(self, dsn: str, org_id: str) -> None:
        self.dsn = dsn
        self.org_id = org_id

    def get(self, system: str, entity_kind: str, entity_id: Any) -> dict[str, Any] | None:
        with db.connect(self.dsn) as conn:
            row = db.fetch_one(conn, "SELECT * FROM external_refs WHERE system = %s AND entity_kind = %s AND entity_id = %s", (system, entity_kind, str(entity_id)))
        return db.jsonable(row)

    def by_external(self, system: str, entity_kind: str, external_id: str) -> dict[str, Any] | None:
        with db.connect(self.dsn) as conn:
            row = db.fetch_one(conn, "SELECT * FROM external_refs WHERE system = %s AND entity_kind = %s AND external_id = %s", (system, entity_kind, external_id))
        return db.jsonable(row)

    def set(self, system: str, entity_kind: str, entity_id: Any, external_id: str, etag: str = "", meta: dict | None = None, error: str = "") -> None:
        with db.connect(self.dsn) as conn:
            db.execute(
                conn,
                """
                INSERT INTO external_refs (org_id, system, entity_kind, entity_id, external_id, etag, version, meta, synced_at, last_error)
                VALUES (%s, %s, %s, %s, %s, %s, 1, %s, now(), %s)
                ON CONFLICT (system, entity_kind, entity_id) DO UPDATE SET
                  external_id = EXCLUDED.external_id, etag = EXCLUDED.etag, version = external_refs.version + 1,
                  meta = COALESCE(external_refs.meta, '{}'::jsonb) || EXCLUDED.meta, synced_at = now(), last_error = EXCLUDED.last_error
                """,
                (self.org_id, system, entity_kind, str(entity_id), external_id, etag, db.J(meta or {}), error),
            )

    def fail(self, system: str, entity_kind: str, entity_id: Any, error: str) -> None:
        with db.connect(self.dsn) as conn:
            db.execute(conn, "UPDATE external_refs SET last_error = %s WHERE system = %s AND entity_kind = %s AND entity_id = %s", (error[:400], system, entity_kind, str(entity_id)))

    def drop(self, system: str, entity_kind: str, entity_id: Any) -> None:
        with db.connect(self.dsn) as conn:
            db.execute(conn, "DELETE FROM external_refs WHERE system = %s AND entity_kind = %s AND entity_id = %s", (system, entity_kind, str(entity_id)))

    def all_for(self, system: str, entity_kind: str) -> list[dict[str, Any]]:
        with db.connect(self.dsn) as conn:
            rows = db.fetch_all(conn, "SELECT * FROM external_refs WHERE system = %s AND entity_kind = %s", (system, entity_kind))
        return [db.jsonable(r) or {} for r in rows]


class ConnectorState:
    """Curseur, santé et coupe-circuit par système."""

    def __init__(self, dsn: str, org_id: str) -> None:
        self.dsn = dsn
        self.org_id = org_id

    def get(self, system: str) -> dict[str, Any]:
        with db.connect(self.dsn) as conn:
            row = db.fetch_one(conn, "SELECT * FROM connector_state WHERE system = %s", (system,))
            if not row:
                db.execute(conn, "INSERT INTO connector_state (system, org_id) VALUES (%s, %s) ON CONFLICT DO NOTHING", (system, self.org_id))
                row = db.fetch_one(conn, "SELECT * FROM connector_state WHERE system = %s", (system,))
        return db.jsonable(row) or {}

    def cursor(self, system: str) -> dict[str, Any]:
        return dict(self.get(system).get("cursor") or {})

    def ok(self, system: str, cursor: dict[str, Any] | None = None, meta: dict | None = None) -> None:
        with db.connect(self.dsn) as conn:
            db.execute(
                conn,
                """
                INSERT INTO connector_state (system, org_id, cursor, status, failures, last_ok_at, last_error, meta, updated_at)
                VALUES (%s, %s, %s, 'ok', 0, now(), '', %s, now())
                ON CONFLICT (system) DO UPDATE SET
                  cursor = COALESCE(%s, connector_state.cursor), status = 'ok', failures = 0, last_ok_at = now(), last_error = '',
                  paused_until = NULL, meta = connector_state.meta || EXCLUDED.meta, updated_at = now()
                """,
                (system, self.org_id, db.J(cursor or {}), db.J(meta or {}), db.J(cursor) if cursor is not None else None),
            )

    def error(self, system: str, error: str) -> dict[str, Any]:
        with db.connect(self.dsn) as conn:
            row = db.fetch_one(
                conn,
                """
                INSERT INTO connector_state (system, org_id, status, failures, last_error, updated_at)
                VALUES (%s, %s, 'error', 1, %s, now())
                ON CONFLICT (system) DO UPDATE SET
                  failures = connector_state.failures + 1,
                  status = CASE WHEN connector_state.failures + 1 >= %s THEN 'paused' ELSE 'error' END,
                  paused_until = CASE WHEN connector_state.failures + 1 >= %s THEN now() + make_interval(secs => %s) ELSE NULL END,
                  last_error = %s, updated_at = now()
                RETURNING *
                """,
                (system, self.org_id, error[:400], PAUSE_AFTER_FAILURES, PAUSE_AFTER_FAILURES, PAUSE_SECONDS, error[:400]),
            )
        state = db.jsonable(row) or {}
        if state.get("status") == "paused":
            log.warning("connecteur %s en pause après %s échecs", system, state.get("failures"))
        return state

    def available(self, system: str) -> bool:
        state = self.get(system)
        if state.get("status") == "disabled":
            return False
        paused = state.get("paused_until")
        if state.get("status") == "paused" and paused:
            with db.connect(self.dsn) as conn:
                still = db.scalar(conn, "SELECT paused_until > now() FROM connector_state WHERE system = %s", (system,))
            return not bool(still)
        return True

    def set_status(self, system: str, status: str) -> None:
        with db.connect(self.dsn) as conn:
            db.execute(conn, "INSERT INTO connector_state (system, org_id, status) VALUES (%s, %s, %s) ON CONFLICT (system) DO UPDATE SET status = EXCLUDED.status, failures = 0, paused_until = NULL, updated_at = now()", (system, self.org_id, status))

    def all(self) -> list[dict[str, Any]]:
        with db.connect(self.dsn) as conn:
            rows = db.fetch_all(conn, "SELECT * FROM connector_state ORDER BY system")
        return [db.jsonable(r) or {} for r in rows]


class Connectors:
    """Registre des connecteurs et exécution protégée (coupe-circuit)."""

    def __init__(self, dsn: str, org_id: str) -> None:
        self.refs = ExternalRefs(dsn, org_id)
        self.state = ConnectorState(dsn, org_id)
        self._items: dict[str, Connector] = {}

    def register(self, connector: Connector) -> Connector:
        self._items[connector.system] = connector
        return connector

    def get(self, system: str) -> Connector:
        try:
            return self._items[system]
        except KeyError as exc:
            raise KeyError(f"connecteur inconnu : {system}") from exc

    def has(self, system: str) -> bool:
        return system in self._items

    def all(self) -> list[Connector]:
        return list(self._items.values())

    def guarded(self, system: str, fn, *args: Any, **kwargs: Any) -> Any:
        """Exécute une opération de connecteur en tenant le coupe-circuit à jour."""
        if not self.state.available(system):
            raise RuntimeError(f"{system} : connecteur en pause")
        try:
            out = fn(*args, **kwargs)
        except Exception as exc:
            self.state.error(system, str(exc))
            raise
        return out

    def health(self) -> list[dict[str, Any]]:
        states = {s["system"]: s for s in self.state.all()}
        out = []
        for connector in self._items.values():
            st = states.get(connector.system, {})
            out.append(
                {
                    "system": connector.system,
                    "label": connector.label,
                    "configured": connector.configured(),
                    "read_only": connector.read_only,
                    "status": st.get("status") or ("idle" if connector.configured() else "disabled"),
                    "failures": st.get("failures") or 0,
                    "last_ok_at": st.get("last_ok_at"),
                    "last_error": st.get("last_error") or "",
                    "paused_until": st.get("paused_until"),
                    "cursor": st.get("cursor") or {},
                }
            )
        return out

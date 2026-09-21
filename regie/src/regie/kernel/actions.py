from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

from regie.kernel import db
from regie.kernel.events import EventBus
from regie.kernel.jobs import P_ACTION, JobContext, JobRunner

log = logging.getLogger("regie.actions")


@dataclass
class ActionSpec:
    """Une action métier déclarée : comment l'appliquer, comment l'annuler.

    `apply(ctx)` reçoit `ActionCtx` (ids, params, before) et renvoie `after`.
    `revert(ctx)` remet l'état `before`. Si `lane` est donné, l'application est asynchrone (job P0).
    """

    kind: str
    module: str
    entity_kind: str
    label: str
    apply: Callable[["ActionCtx"], dict[str, Any]]
    revert: Callable[["ActionCtx"], None] | None = None
    snapshot: Callable[[list[str]], dict[str, Any]] | None = None
    prepare: Callable[["ActionCtx"], None] | None = None
    lane: str | None = None
    undoable: bool = True
    needs_confirmation: bool = False
    inverse: str | None = None


@dataclass
class ActionCtx:
    action_id: int
    kind: str
    ids: list[str]
    params: dict[str, Any]
    before: dict[str, Any]
    after: dict[str, Any]
    actor_id: str | None
    job: JobContext | None = None


def public_action(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "module": row.get("module") or "",
        "kind": row.get("kind") or "",
        "label": row.get("label") or "",
        "entity_kind": row.get("entity_kind") or "",
        "item_ids": list(row.get("entity_ids") or []),
        "params": row.get("params") or {},
        "status": row.get("status") or "pending",
        "error": row.get("error") or "",
        "actor_id": str(row["actor_id"]) if row.get("actor_id") else None,
        "undo_of": row.get("undo_of"),
        "job_id": row.get("job_id"),
        "created_at": db.iso(row.get("created_at")),
        "done_at": db.iso(row.get("done_at")),
        "undone_at": db.iso(row.get("undone_at")),
        "after": row.get("after") or {},
        "reversible": (row.get("status") in {"pending", "done"}) and bool(row.get("_undoable", True)) and not row.get("undo_of"),
    }


class Actions:
    """Journal réversible commun à tous les modules."""

    def __init__(self, dsn: str, org_id: str, runner: JobRunner, bus: EventBus) -> None:
        self.dsn = dsn
        self.org_id = org_id
        self.runner = runner
        self.bus = bus
        self._specs: dict[str, ActionSpec] = {}
        runner.register("action", self._job_apply, "actions", concurrency=1)

    def register(self, spec: ActionSpec) -> ActionSpec:
        if spec.kind in self._specs:
            raise ValueError(f"action déjà déclarée : {spec.kind}")
        self._specs[spec.kind] = spec
        if spec.lane:
            self.runner._lanes.setdefault(spec.lane, 1)
        return spec

    def spec(self, kind: str) -> ActionSpec:
        try:
            return self._specs[kind]
        except KeyError as exc:
            raise KeyError(f"action inconnue : {kind}") from exc

    def kinds(self, module: str | None = None) -> list[dict[str, Any]]:
        return [
            {"kind": s.kind, "module": s.module, "entity_kind": s.entity_kind, "label": s.label, "undoable": s.undoable, "async": bool(s.lane), "needs_confirmation": s.needs_confirmation}
            for s in self._specs.values()
            if module is None or s.module == module
        ]

    # --- exécution ---------------------------------------------------------------------

    def perform(self, kind: str, ids: list[Any], params: dict[str, Any] | None = None, actor_id: str | None = None, label: str | None = None, undo_of: int | None = None) -> dict[str, Any]:
        spec = self.spec(kind)
        clean = [str(i) for i in ids if str(i).strip()]
        if not clean:
            raise ValueError("aucune entité")
        params = dict(params or {})
        before = spec.snapshot(clean) if spec.snapshot else {}
        text = label or (spec.label + (f" · {len(clean)} éléments" if len(clean) > 1 else ""))
        with db.connect(self.dsn) as conn:
            row = db.fetch_one(
                conn,
                """
                INSERT INTO actions (org_id, module, kind, label, entity_kind, entity_ids, params, before, status, actor_id, undo_of)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'pending', %s, %s) RETURNING *
                """,
                (self.org_id, spec.module, kind, text[:200], spec.entity_kind, db.J(clean), db.J(params), db.J(before), actor_id, undo_of),
            )
        assert row is not None
        action_id = int(row["id"])
        ctx = ActionCtx(action_id, kind, clean, params, before, {}, actor_id)
        if spec.prepare:
            try:
                spec.prepare(ctx)
            except Exception as exc:
                with db.connect(self.dsn) as conn:
                    db.execute(conn, "UPDATE actions SET status = 'failed', error = %s, done_at = now() WHERE id = %s", (str(exc)[:400], action_id))
                raise
        if spec.lane:
            job = self.runner.submit("action", {"action_id": action_id}, P_ACTION, lane=spec.lane, created_by=actor_id)
            with db.connect(self.dsn) as conn:
                row = db.fetch_one(conn, "UPDATE actions SET job_id = %s WHERE id = %s RETURNING *", (int(job["id"]), action_id))
            assert row is not None
            packed = self._public(row, spec)
            self.bus.publish("action", {"action": packed, "status": "pending"})
            return {**packed, "job_id": int(job["id"])}
        return self._run(action_id, spec, ctx, None)

    def _job_apply(self, job: JobContext) -> dict[str, Any]:
        action_id = int(job.payload.get("action_id") or 0)
        with db.connect(self.dsn) as conn:
            row = db.fetch_one(conn, "SELECT * FROM actions WHERE id = %s", (action_id,))
        if not row:
            raise ValueError("action introuvable")
        if row["status"] != "pending":
            return {"skipped": True}
        spec = self.spec(str(row["kind"]))
        ctx = ActionCtx(action_id, spec.kind, [str(i) for i in row["entity_ids"]], dict(row["params"] or {}), dict(row["before"] or {}), {}, str(row["actor_id"]) if row.get("actor_id") else None, job)
        result = self._run(action_id, spec, ctx, job)
        if result.get("status") == "failed":
            raise RuntimeError(result.get("error") or "action en échec")
        return {"action": result}

    def _run(self, action_id: int, spec: ActionSpec, ctx: ActionCtx, job: JobContext | None) -> dict[str, Any]:
        try:
            after = spec.apply(ctx) or {}
            with db.connect(self.dsn) as conn:
                row = db.fetch_one(conn, "UPDATE actions SET status = 'done', after = %s, done_at = now(), error = '' WHERE id = %s RETURNING *", (db.J(after), action_id))
            assert row is not None
            packed = self._public(row, spec)
            self.bus.publish("action", {"action": packed, "status": "done"})
            return packed
        except Exception as exc:
            log.warning("action %s #%s : %s", spec.kind, action_id, exc)
            if spec.revert and ctx.before:
                try:
                    spec.revert(ctx)
                except Exception as again:
                    log.warning("retour arrière %s : %s", spec.kind, again)
            with db.connect(self.dsn) as conn:
                row = db.fetch_one(conn, "UPDATE actions SET status = 'failed', error = %s, done_at = now() WHERE id = %s RETURNING *", (str(exc)[:400], action_id))
            assert row is not None
            packed = self._public(row, spec)
            self.bus.publish("action", {"action": packed, "status": "failed"})
            return packed

    def undo(self, action_id: int, actor_id: str | None = None) -> dict[str, Any]:
        with db.connect(self.dsn) as conn:
            row = db.fetch_one(conn, "SELECT * FROM actions WHERE id = %s", (action_id,))
        if not row:
            raise KeyError("action introuvable")
        if row["status"] not in {"pending", "done"}:
            raise ValueError("action déjà annulée ou échouée")
        spec = self.spec(str(row["kind"]))
        if not spec.undoable:
            raise ValueError("action non annulable")
        ids = [str(i) for i in row["entity_ids"]]
        params = dict(row["params"] or {})
        result: dict[str, Any]
        if spec.inverse:
            inverse = self.spec(spec.inverse)
            result = self.perform(inverse.kind, ids, {**params, "undo_before": row["before"]}, actor_id, f"Annulé : {row['label']}", undo_of=action_id)
        elif spec.revert:
            ctx = ActionCtx(action_id, spec.kind, ids, params, dict(row["before"] or {}), dict(row["after"] or {}), actor_id)
            spec.revert(ctx)
            result = {"reverted": True}
        else:
            raise ValueError("action non annulable")
        with db.connect(self.dsn) as conn:
            updated = db.fetch_one(conn, "UPDATE actions SET status = 'undone', undone_at = now() WHERE id = %s RETURNING *", (action_id,))
        if updated:
            self.bus.publish("action", {"action": self._public(updated, spec), "status": "undone"})
        return {**result, "undone_id": action_id}

    # --- lecture -----------------------------------------------------------------------------

    def _public(self, row: dict[str, Any], spec: ActionSpec | None = None) -> dict[str, Any]:
        spec = spec or self._specs.get(str(row.get("kind") or ""))
        return public_action({**row, "_undoable": bool(spec.undoable) if spec else False})

    def get(self, action_id: int) -> dict[str, Any] | None:
        with db.connect(self.dsn) as conn:
            row = db.fetch_one(conn, "SELECT * FROM actions WHERE id = %s", (action_id,))
        return self._public(row) if row else None

    def recent(self, limit: int = 40, module: str | None = None, entity_kind: str | None = None, entity_id: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM actions WHERE org_id = %s"
        args: list[Any] = [self.org_id]
        if module:
            sql += " AND module = %s"
            args.append(module)
        if entity_kind:
            sql += " AND entity_kind = %s"
            args.append(entity_kind)
        if entity_id is not None:
            sql += " AND entity_ids @> %s"
            args.append(db.J([str(entity_id)]))
        sql += " ORDER BY id DESC LIMIT %s"
        args.append(limit)
        with db.connect(self.dsn) as conn:
            rows = db.fetch_all(conn, sql, args)
        return [self._public(row) for row in rows]

    def last_undoable(self, actor_id: str | None = None) -> dict[str, Any] | None:
        for row in self.recent(20):
            if row["reversible"] and (actor_id is None or row.get("actor_id") in (None, actor_id)):
                return row
        return None

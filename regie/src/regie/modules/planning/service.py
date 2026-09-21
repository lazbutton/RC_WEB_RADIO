"""Planning : tâches (kanban, assignation multiple, checklists), commentaires sur toute entité, disponibilités, tableau de bord."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from regie.kernel import db
from regie.kernel.actions import ActionCtx, ActionSpec
from regie.kernel.core import Kernel
from regie.kernel.crud import Table
from regie.kernel.registry import EntityKind

TASK_COLUMNS = {"title": "text", "description": "text", "status": "text", "priority": "int", "due_at": "ts", "start_at": "ts", "all_day": "bool", "assignees": "array", "checklist": "json", "tags": "array", "position": "int", "created_by": "uuid", "done_at": "ts"}
STATUSES = ("todo", "doing", "done", "cancelled")


class PlanningService:
    name = "planning"

    def __init__(self, kernel: Kernel) -> None:
        self.kernel = kernel
        self.tasks = Table(kernel.dsn, kernel.org_id, "tasks", TASK_COLUMNS, order="status, position, due_at NULLS LAST, id DESC")
        self._register()

    def _register(self) -> None:
        k = self.kernel
        k.registry.register(EntityKind(kind="task", module="planning", label="Tâche", icon="check", table="tasks", fetch=self.tasks.many, summarize=lambda r: {"title": r.get("title") or "", "subtitle": f"{r.get('status')}" + (f" · {str(r.get('due_at'))[:10]}" if r.get("due_at") else ""), "status": r.get("status")}, url=lambda i: f"/planning?task={i}", actions=["planning.done"]))
        k.actions.register(ActionSpec(kind="planning.done", module="planning", entity_kind="task", label="Tâche terminée", apply=self._done_apply, revert=self._done_revert, snapshot=lambda ids: {i: (self.tasks.get(int(i)) or {}).get("status") for i in ids}, undoable=True))
        k.actions.register(ActionSpec(kind="planning.task_from_mail", module="planning", entity_kind="mail", label="Tâche créée depuis un mail", apply=self._task_from_mail, revert=self._delete_created, undoable=True))
        k.outbox.on("coverage.planned", self._on_coverage_planned)

    # --- tâches ------------------------------------------------------------------------------

    def create(self, data: dict[str, Any], actor: str | None = None, link_to: tuple[str, Any] | None = None) -> dict[str, Any]:
        payload = dict(data)
        payload.setdefault("status", "todo")
        if payload["status"] not in STATUSES:
            raise ValueError("statut inconnu")
        payload["created_by"] = actor
        if not payload.get("title"):
            raise ValueError("titre requis")
        payload["position"] = self._next_position(payload["status"])
        row = self.tasks.create(payload)
        if link_to:
            self.kernel.links.link("task", row["id"], link_to[0], link_to[1], role="about", actor=actor)
        self._index(row)
        self._notify_assignees(row, actor, "t'a assigné une tâche")
        self.kernel.outbox.emit("task.created", {"id": row["id"], "title": row["title"], "actor": actor})
        return self._public(row)

    def update(self, task_id: int, data: dict[str, Any], actor: str | None = None) -> dict[str, Any] | None:
        current = self.tasks.get(task_id)
        if not current:
            return None
        payload = {k: v for k, v in data.items() if k in TASK_COLUMNS and k not in {"created_by"}}
        if "status" in payload:
            if payload["status"] not in STATUSES:
                raise ValueError("statut inconnu")
            if payload["status"] == "done" and current.get("status") != "done":
                payload["done_at"] = db.utcnow()
            elif payload["status"] != "done":
                payload["done_at"] = None
            if payload["status"] != current.get("status") and "position" not in payload:
                payload["position"] = self._next_position(payload["status"])
        row = self.tasks.update(task_id, payload)
        if row:
            self._index(row)
            new_assignees = set(row.get("assignees") or []) - set(current.get("assignees") or [])
            if new_assignees:
                self._notify_assignees({**row, "assignees": list(new_assignees)}, actor, "t'a assigné une tâche")
            self.kernel.outbox.emit("task.updated", {"id": task_id, "status": row.get("status"), "actor": actor})
        return self._public(row) if row else None

    def delete(self, task_id: int) -> bool:
        ok = self.tasks.delete(task_id)
        if ok:
            self.kernel.links.purge("task", task_id)
            self.kernel.search.remove("task", task_id)
            self.kernel.outbox.emit("task.deleted", {"id": task_id})
        return ok

    def reorder(self, status: str, ordered_ids: list[int]) -> None:
        if status not in STATUSES:
            raise ValueError("statut inconnu")
        with db.connect(self.kernel.dsn) as conn:
            for index, task_id in enumerate(ordered_ids):
                db.execute(conn, "UPDATE tasks SET position = %s, status = %s WHERE id = %s AND org_id = %s", (index, status, task_id, self.kernel.org_id))
        self.kernel.bus.publish("tasks", {"reason": "reorder"})

    def toggle_check(self, task_id: int, index: int, done: bool) -> dict[str, Any] | None:
        row = self.tasks.get(task_id)
        if not row:
            return None
        checklist = list(row.get("checklist") or [])
        if not 0 <= index < len(checklist):
            raise ValueError("élément de checklist inconnu")
        checklist[index] = {**checklist[index], "done": done}
        return self.update(task_id, {"checklist": checklist})

    def list(self, *, status: str | None = None, assignee: str | None = None, q: str = "", entity: tuple[str, str] | None = None, include_done: bool = True, limit: int = 500) -> list[dict[str, Any]]:
        where: list[str] = []
        params: list[Any] = []
        if status:
            where.append("status = %s")
            params.append(status)
        elif not include_done:
            where.append("status IN ('todo', 'doing')")
        if assignee:
            where.append("%s = ANY(assignees)")
            params.append(assignee)
        if q.strip():
            where.append("regie_unaccent(title) ILIKE regie_unaccent(%s)")
            params.append(f"%{q.strip()}%")
        if entity:
            ids = self.kernel.links.targets(entity[0], entity[1], "task")
            if not ids:
                return []
            where.append("id = ANY(%s)")
            params.append([int(i) for i in ids])
        rows = self.tasks.list(" AND ".join(where), params, limit)
        return [self._public(r) for r in rows]

    def _public(self, row: dict[str, Any]) -> dict[str, Any]:
        out = dict(row)
        checklist = list(out.get("checklist") or [])
        out["checklist_done"] = sum(1 for c in checklist if c.get("done"))
        out["checklist_total"] = len(checklist)
        due = row.get("due_at")
        out["overdue"] = bool(due and row.get("status") in {"todo", "doing"} and str(due) < db.iso(db.utcnow()))
        return out

    def _next_position(self, status: str) -> int:
        with db.connect(self.kernel.dsn) as conn:
            return int(db.scalar(conn, "SELECT COALESCE(MAX(position), -1) + 1 FROM tasks WHERE org_id = %s AND status = %s", (self.kernel.org_id, status)) or 0)

    def _index(self, row: dict[str, Any]) -> None:
        self.kernel.search.index("task", row["id"], row.get("title") or "", row.get("status") or "", " ".join([row.get("description") or "", " ".join(row.get("tags") or [])]), url=f"/planning?task={row['id']}")

    def _notify_assignees(self, row: dict[str, Any], actor: str | None, verb: str) -> None:
        targets = [str(u) for u in (row.get("assignees") or []) if str(u) != str(actor)]
        if targets:
            who = (self.kernel.auth.user(actor) or {}).get("name") if actor else "Quelqu'un"
            self.kernel.notifications.notify(targets, "task", f"{who or 'Quelqu’un'} {verb} : {row.get('title')}", "task", row["id"], f"/planning?task={row['id']}")

    # --- actions -------------------------------------------------------------------------------

    def _done_apply(self, ctx: ActionCtx) -> dict[str, Any]:
        for task_id in ctx.ids:
            self.update(int(task_id), {"status": "done"}, actor=ctx.actor_id)
        return {"done": ctx.ids}

    def _done_revert(self, ctx: ActionCtx) -> None:
        for task_id, previous in ctx.before.items():
            self.update(int(task_id), {"status": previous or "todo"}, actor=ctx.actor_id)

    def _task_from_mail(self, ctx: ActionCtx) -> dict[str, Any]:
        mail = self.kernel.modules.get("mail")
        if mail is None:
            raise RuntimeError("module Mails absent")
        created = []
        for item_id in ctx.ids:
            item = mail.store.get_item(int(item_id))
            if not item:
                continue
            task = self.create({"title": ctx.params.get("title") or item.get("subject") or "(sans objet)", "description": (item.get("summary_full") or item.get("summary") or "")[:2000], "assignees": ctx.params.get("assignees") or ([ctx.actor_id] if ctx.actor_id else []), "due_at": ctx.params.get("due_at")}, actor=ctx.actor_id, link_to=("mail", item_id))
            created.append(int(task["id"]))
        return {"created": created}

    def _delete_created(self, ctx: ActionCtx) -> None:
        for task_id in ctx.after.get("created") or []:
            self.delete(int(task_id))

    def _on_coverage_planned(self, event: dict[str, Any]) -> None:
        """Une couverture planifiée crée sa tâche, liée à l'événement, si elle n'existe pas déjà."""
        cov_id = event.get("id")
        if not cov_id or self.kernel.links.targets("coverage", cov_id, "task"):
            return
        events = self.kernel.modules.get("events")
        title = f"Couvrir ({event.get('kind')})"
        if events is not None:
            row = events.get(str(event.get("event_id")))
            if row:
                title = f"{event.get('kind', 'couverture').capitalize()} · {row['title']}"
        assignee = event.get("assignee_id")
        task = self.create({"title": title, "assignees": [assignee] if assignee else [], "tags": ["couverture"]}, actor=assignee, link_to=("coverage", cov_id))
        if event.get("event_id"):
            self.kernel.links.link("task", task["id"], "event", event["event_id"], role="about")

    # --- commentaires -----------------------------------------------------------------------------

    def comment(self, entity_kind: str, entity_id: Any, author_id: str | None, body: str) -> dict[str, Any]:
        if not body.strip():
            raise ValueError("commentaire vide")
        if not self.kernel.registry.has(entity_kind):
            raise ValueError("type inconnu")
        with db.connect(self.kernel.dsn) as conn:
            row = db.fetch_one(conn, "INSERT INTO comments (org_id, entity_kind, entity_id, author_id, body) VALUES (%s, %s, %s, %s, %s) RETURNING *", (self.kernel.org_id, entity_kind, str(entity_id), author_id, body.strip()[:4000]))
        out = db.jsonable(row) or {}
        if entity_kind == "task":
            task = self.tasks.get(int(entity_id))
            if task:
                targets = {str(u) for u in (task.get("assignees") or [])} | ({str(task["created_by"])} if task.get("created_by") else set())
                targets.discard(str(author_id))
                if targets:
                    who = (self.kernel.auth.user(author_id) or {}).get("name") if author_id else "Quelqu'un"
                    self.kernel.notifications.notify(sorted(targets), "comment", f"{who or 'Quelqu’un'} a commenté : {task.get('title')}", "task", entity_id, f"/planning?task={entity_id}")
        self.kernel.outbox.emit("comment.created", {"id": out.get("id"), "entity_kind": entity_kind, "entity_id": str(entity_id)})
        return out

    def comments(self, entity_kind: str, entity_id: Any) -> list[dict[str, Any]]:
        with db.connect(self.kernel.dsn) as conn:
            rows = db.fetch_all(conn, "SELECT c.*, u.name AS author_name FROM comments c LEFT JOIN users u ON u.id = c.author_id WHERE c.entity_kind = %s AND c.entity_id = %s ORDER BY c.created_at", (entity_kind, str(entity_id)))
        return [db.jsonable(r) or {} for r in rows]

    def delete_comment(self, comment_id: int, user: dict[str, Any]) -> bool:
        with db.connect(self.kernel.dsn) as conn:
            row = db.fetch_one(conn, "SELECT * FROM comments WHERE id = %s", (comment_id,))
            if not row:
                return False
            if user.get("role") != "admin" and str(row.get("author_id")) != str(user.get("id")):
                raise PermissionError("pas ton commentaire")
            return db.execute(conn, "DELETE FROM comments WHERE id = %s", (comment_id,)) > 0

    # --- disponibilités ------------------------------------------------------------------------------

    def availability(self, user_id: str | None = None) -> dict[str, Any]:
        with db.connect(self.kernel.dsn) as conn:
            if user_id:
                slots = db.fetch_all(conn, "SELECT * FROM availability WHERE org_id = %s AND user_id = %s ORDER BY weekday, start_time", (self.kernel.org_id, user_id))
                absences = db.fetch_all(conn, "SELECT * FROM absences WHERE org_id = %s AND user_id = %s AND end_date >= current_date ORDER BY start_date", (self.kernel.org_id, user_id))
            else:
                slots = db.fetch_all(conn, "SELECT * FROM availability WHERE org_id = %s ORDER BY user_id, weekday, start_time", (self.kernel.org_id,))
                absences = db.fetch_all(conn, "SELECT * FROM absences WHERE org_id = %s AND end_date >= current_date ORDER BY start_date", (self.kernel.org_id,))
        return {"slots": [self._time_row(r) for r in slots], "absences": [db.jsonable(r) for r in absences]}

    def _time_row(self, row: dict[str, Any]) -> dict[str, Any]:
        out = db.jsonable(row) or {}
        for key in ("start_time", "end_time"):
            if row.get(key) is not None:
                out[key] = row[key].strftime("%H:%M")
        return out

    def set_availability(self, user_id: str, slots: list[dict[str, Any]]) -> dict[str, Any]:
        with db.connect(self.kernel.dsn) as conn:
            db.execute(conn, "DELETE FROM availability WHERE org_id = %s AND user_id = %s", (self.kernel.org_id, user_id))
            for slot in slots:
                db.execute(conn, "INSERT INTO availability (org_id, user_id, weekday, start_time, end_time, note) VALUES (%s, %s, %s, %s, %s, %s)", (self.kernel.org_id, user_id, int(slot["weekday"]), slot["start_time"], slot["end_time"], str(slot.get("note") or "")))
        return self.availability(user_id)

    def add_absence(self, user_id: str, start_date: str, end_date: str, reason: str = "") -> dict[str, Any]:
        with db.connect(self.kernel.dsn) as conn:
            row = db.fetch_one(conn, "INSERT INTO absences (org_id, user_id, start_date, end_date, reason) VALUES (%s, %s, %s, %s, %s) RETURNING *", (self.kernel.org_id, user_id, start_date, end_date, reason[:200]))
        return db.jsonable(row) or {}

    def remove_absence(self, absence_id: int, user_id: str) -> bool:
        with db.connect(self.kernel.dsn) as conn:
            return db.execute(conn, "DELETE FROM absences WHERE id = %s AND user_id = %s", (absence_id, user_id)) > 0

    # --- tableau de bord et Semaine -------------------------------------------------------------------

    def today(self, user: dict[str, Any]) -> dict[str, Any]:
        k = self.kernel
        now = db.utcnow()
        start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc) - timedelta(hours=2)
        end = start + timedelta(days=1, hours=4)
        mine = self.list(assignee=user["id"], include_done=False)
        overdue = [t for t in mine if t["overdue"]]
        due_today = [t for t in mine if t.get("due_at") and str(t["due_at"])[:10] == db.iso(now)[:10]]
        calendar = k.modules.get("calendar")
        appointments = calendar.range(db.iso(start), db.iso(end), None if user["role"] == "admin" else user["id"]) if calendar else []
        events = k.modules.get("events")
        week_end = start + timedelta(days=7)
        agenda = events.agenda(db.iso(start), db.iso(week_end)) if events else []
        covered = [e for e in agenda if e.get("coverage") or e.get("is_radio_campus")]
        mail = k.modules.get("mail")
        mail_counts = mail.store.counts() if mail else {}
        with db.connect(k.dsn) as conn:
            changes = db.fetch_all(conn, "SELECT topic, payload, created_at FROM outbox WHERE org_id = %s AND created_at > now() - interval '24 hours' AND topic NOT LIKE 'settings.%%' ORDER BY id DESC LIMIT 40", (k.org_id,))
        return {
            "user": {"id": user["id"], "name": user.get("name")},
            "tasks": {"mine": mine[:20], "overdue": overdue, "due_today": due_today, "doing": [t for t in mine if t["status"] == "doing"]},
            "appointments": appointments,
            "covered_events": covered[:12],
            "mail": {"proposed": mail_counts.get("proposed", 0), "unread": mail.store.unread_count() if mail else 0},
            "notifications": k.notifications.list(user["id"], unread_only=True, limit=10),
            "changes": [db.jsonable(c) for c in changes],
            "jobs": {"late": k.jobs.snapshot()["late"], "dead": k.jobs.snapshot()["dead"]},
            "connectors": [c for c in k.connectors.health() if c["configured"]],
            "backup_age_hours": k.backup_age_hours(),
        }

    def week(self, start: str, user: dict[str, Any]) -> dict[str, Any]:
        """Semaine fusionnée : rendez-vous Google, événements couverts, tâches datées, absences."""
        k = self.kernel
        begin = _dt(start) or db.utcnow()
        end = begin + timedelta(days=7)
        calendar = k.modules.get("calendar")
        events = k.modules.get("events")
        with db.connect(k.dsn) as conn:
            tasks = db.fetch_all(conn, "SELECT * FROM tasks WHERE org_id = %s AND status IN ('todo', 'doing') AND due_at >= %s AND due_at < %s ORDER BY due_at", (k.org_id, begin, end))
            absences = db.fetch_all(conn, "SELECT a.*, u.name FROM absences a JOIN users u ON u.id = a.user_id WHERE a.org_id = %s AND a.end_date >= %s::date AND a.start_date < %s::date", (k.org_id, begin.date(), end.date()))
        return {
            "start": db.iso(begin),
            "end": db.iso(end),
            "appointments": calendar.range(db.iso(begin), db.iso(end), None if user["role"] == "admin" else user["id"]) if calendar else [],
            "events": [e for e in (events.agenda(db.iso(begin), db.iso(end)) if events else []) if e.get("coverage") or e.get("is_radio_campus")],
            "tasks": [self._public(db.jsonable(t) or {}) for t in tasks],
            "absences": [db.jsonable(a) for a in absences],
            "accounts": calendar.accounts(None if user["role"] == "admin" else user["id"]) if calendar else [],
        }


def _dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

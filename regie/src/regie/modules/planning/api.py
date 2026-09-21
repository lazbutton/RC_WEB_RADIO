from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from regie.kernel.api import Problem, require
from regie.modules.planning.service import PlanningService


class TaskIn(BaseModel):
    title: str
    description: str = ""
    status: str = "todo"
    priority: int = 1
    due_at: str | None = None
    start_at: str | None = None
    all_day: bool = True
    assignees: list[str] = []
    checklist: list[dict[str, Any]] = []
    tags: list[str] = []
    link_kind: str | None = None
    link_id: str | None = None


class TaskPatch(BaseModel):
    title: str | None = None
    description: str | None = None
    status: str | None = None
    priority: int | None = None
    due_at: str | None = None
    start_at: str | None = None
    all_day: bool | None = None
    assignees: list[str] | None = None
    checklist: list[dict[str, Any]] | None = None
    tags: list[str] | None = None
    clear_due: bool = False


class ReorderIn(BaseModel):
    status: str
    ids: list[int]


class CheckIn(BaseModel):
    index: int
    done: bool


class CommentIn(BaseModel):
    entity_kind: str
    entity_id: str
    body: str


class FromMailIn(BaseModel):
    ids: list[int]
    title: str | None = None
    due_at: str | None = None
    assignees: list[str] | None = None


class SlotsIn(BaseModel):
    slots: list[dict[str, Any]]


class AbsenceIn(BaseModel):
    start_date: str
    end_date: str
    reason: str = ""


def build_router(service: PlanningService) -> APIRouter:
    r = APIRouter(prefix="/api/v1/planning", tags=["planning"])
    read = require("planning", "read")
    write = require("planning", "write")
    k = service.kernel

    @r.get("/today")
    def today(user: dict = Depends(read)):
        return {"ok": True, **service.today(user)}

    @r.get("/week")
    def week(start: str = "", user: dict = Depends(read)):
        return {"ok": True, **service.week(start, user)}

    @r.get("/tasks")
    def tasks(status: str | None = None, assignee: str | None = None, q: str = "", entity_kind: str | None = None, entity_id: str | None = None, open_only: int = 0, _user: dict = Depends(read)):
        entity = (entity_kind, entity_id) if entity_kind and entity_id else None
        return {"ok": True, "tasks": service.list(status=status, assignee=assignee, q=q, entity=entity, include_done=not open_only), "users": [{"id": u["id"], "name": u["name"]} for u in k.auth.users() if not u["disabled"]]}

    @r.post("/tasks", status_code=201)
    def create(body: TaskIn, user: dict = Depends(write)):
        data = body.model_dump(exclude={"link_kind", "link_id"})
        link = (body.link_kind, body.link_id) if body.link_kind and body.link_id else None
        if link and not k.registry.has(link[0]):
            raise Problem(400, "type lié inconnu")
        try:
            return {"ok": True, "task": service.create(data, actor=user["id"], link_to=link)}
        except ValueError as exc:
            raise Problem(400, str(exc)) from exc

    @r.get("/tasks/{task_id}")
    def get_task(task_id: int, _user: dict = Depends(read)):
        row = service.tasks.get(task_id)
        if not row:
            raise Problem(404, "tâche introuvable")
        links = k.links.of("task", task_id)
        summaries = k.registry.summaries([(str(l["other_kind"]), str(l["other_id"])) for l in links])
        for link in links:
            link["other"] = summaries.get(f"{link['other_kind']}:{link['other_id']}")
        return {"ok": True, "task": service._public(row), "links": links, "comments": service.comments("task", task_id)}

    @r.patch("/tasks/{task_id}")
    def patch(task_id: int, body: TaskPatch, user: dict = Depends(write)):
        data = {key: value for key, value in body.model_dump(exclude={"clear_due"}).items() if value is not None}
        if body.clear_due:
            data["due_at"] = None
        try:
            row = service.update(task_id, data, actor=user["id"])
        except ValueError as exc:
            raise Problem(400, str(exc)) from exc
        if not row:
            raise Problem(404, "tâche introuvable")
        return {"ok": True, "task": row}

    @r.delete("/tasks/{task_id}")
    def delete(task_id: int, _user: dict = Depends(write)):
        if not service.delete(task_id):
            raise Problem(404, "tâche introuvable")
        return {"ok": True}

    @r.post("/tasks/reorder")
    def reorder(body: ReorderIn, _user: dict = Depends(write)):
        try:
            service.reorder(body.status, body.ids)
        except ValueError as exc:
            raise Problem(400, str(exc)) from exc
        return {"ok": True}

    @r.post("/tasks/{task_id}/check")
    def check(task_id: int, body: CheckIn, _user: dict = Depends(write)):
        try:
            row = service.toggle_check(task_id, body.index, body.done)
        except ValueError as exc:
            raise Problem(400, str(exc)) from exc
        if not row:
            raise Problem(404, "tâche introuvable")
        return {"ok": True, "task": row}

    @r.post("/tasks/{task_id}/done")
    def done(task_id: int, user: dict = Depends(write)):
        if not service.tasks.get(task_id):
            raise Problem(404, "tâche introuvable")
        return {"ok": True, "action": k.actions.perform("planning.done", [str(task_id)], {}, actor_id=user["id"])}

    @r.post("/tasks/from-mail")
    def from_mail(body: FromMailIn, user: dict = Depends(write)):
        params = {key: value for key, value in body.model_dump(exclude={"ids"}).items() if value is not None}
        action = k.actions.perform("planning.task_from_mail", [str(i) for i in body.ids], params, actor_id=user["id"])
        if action.get("status") == "failed":
            raise Problem(400, action.get("error") or "création impossible")
        return {"ok": True, "action": action, "tasks": [service._public(t) for t in service.tasks.many(action.get("after", {}).get("created") or [])]}

    @r.get("/comments")
    def comments(entity_kind: str, entity_id: str, _user: dict = Depends(read)):
        return {"ok": True, "comments": service.comments(entity_kind, entity_id)}

    @r.post("/comments", status_code=201)
    def comment(body: CommentIn, user: dict = Depends(write)):
        try:
            return {"ok": True, "comment": service.comment(body.entity_kind, body.entity_id, user["id"], body.body)}
        except ValueError as exc:
            raise Problem(400, str(exc)) from exc

    @r.delete("/comments/{comment_id}")
    def delete_comment(comment_id: int, user: dict = Depends(write)):
        try:
            ok = service.delete_comment(comment_id, user)
        except PermissionError as exc:
            raise Problem(403, str(exc)) from exc
        if not ok:
            raise Problem(404, "commentaire introuvable")
        return {"ok": True}

    @r.get("/availability")
    def availability(user_id: str | None = None, _user: dict = Depends(read)):
        return {"ok": True, **service.availability(user_id)}

    @r.put("/availability")
    def set_availability(body: SlotsIn, user: dict = Depends(write)):
        return {"ok": True, **service.set_availability(user["id"], body.slots)}

    @r.post("/absences", status_code=201)
    def absence(body: AbsenceIn, user: dict = Depends(write)):
        return {"ok": True, "absence": service.add_absence(user["id"], body.start_date, body.end_date, body.reason)}

    @r.delete("/absences/{absence_id}")
    def remove_absence(absence_id: int, user: dict = Depends(write)):
        return {"ok": service.remove_absence(absence_id, user["id"])}

    return r

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from regie.kernel.api import Problem, require
from regie.kernel.jobs import P_USER
from regie.modules.shows.service import ShowsService


class DataIn(BaseModel):
    data: dict[str, Any]


class SegmentIn(BaseModel):
    title: str = ""
    kind: str = "itw"
    start_s: float
    end_s: float
    phrase_in: str = ""
    phrase_out: str = ""
    speaker: str = ""
    coupures: list[Any] = []
    handles: dict[str, float] | None = None
    notes: str = ""
    position: int | None = None


class SegmentPatch(BaseModel):
    title: str | None = None
    kind: str | None = None
    start_s: float | None = None
    end_s: float | None = None
    phrase_in: str | None = None
    phrase_out: str | None = None
    speaker: str | None = None
    coupures: list[Any] | None = None
    handles: dict[str, float] | None = None
    notes: str | None = None
    position: int | None = None


class ValidateIn(BaseModel):
    ids: list[int]
    valid: bool = True


class PodcastIn(BaseModel):
    episode_id: int | None = None
    segment_id: int | None = None
    title: str = ""
    description: str = ""
    rights_status: str = "unknown"
    rights_notes: str = ""


class PodcastPatch(BaseModel):
    title: str | None = None
    description: str | None = None
    rights_status: str | None = None
    rights_notes: str | None = None
    cover_path: str | None = None


class ExportIn(BaseModel):
    format: str = "mp3"


def build_router(service: ShowsService) -> APIRouter:
    r = APIRouter(prefix="/api/v1/shows", tags=["shows"])
    read = require("shows", "read")
    write = require("shows", "write")
    k = service.kernel

    @r.get("")
    def list_shows(_user: dict = Depends(read)):
        shows = service.shows.list("", [], 200)
        counts = {row["show_id"]: int(row["n"]) for row in _episode_counts(service)}
        for show in shows:
            show["episodes"] = counts.get(show["id"], 0)
        return {"ok": True, "shows": shows, "media_available": k.files.available}

    @r.post("", status_code=201)
    def create_show(body: DataIn, _user: dict = Depends(write)):
        if not body.data.get("name"):
            raise Problem(400, "nom requis")
        return {"ok": True, "show": service.create_show(body.data)}

    @r.patch("/{show_id}")
    def patch_show(show_id: int, body: DataIn, _user: dict = Depends(write)):
        row = service.update_show(show_id, body.data)
        if not row:
            raise Problem(404, "émission introuvable")
        return {"ok": True, "show": row}

    @r.post("/detect", status_code=202)
    def detect(_user: dict = Depends(write)):
        if not k.files.available:
            raise Problem(503, "Médiathèque non montée.")
        return {"ok": True, "job": k.jobs.submit("shows.detect", {}, P_USER, dedupe=True)}

    @r.get("/episodes")
    def episodes(show_id: int | None = None, status: str | None = None, limit: int = 100, _user: dict = Depends(read)):
        where, params = [], []
        if show_id:
            where.append("show_id = %s")
            params.append(show_id)
        if status:
            where.append("status = %s")
            params.append(status)
        rows = service.episodes.list(" AND ".join(where), params, max(1, min(500, limit)))
        for row in rows:
            row.pop("waveform", None)
        shows = {s["id"]: s for s in service.shows.many(list({r["show_id"] for r in rows}))}
        for row in rows:
            row["show"] = shows.get(row["show_id"])
        return {"ok": True, "episodes": rows}

    @r.get("/episodes/{episode_id}")
    def episode(episode_id: int, _user: dict = Depends(read)):
        view = service.episode_view(episode_id)
        if not view:
            raise Problem(404, "épisode introuvable")
        return {"ok": True, **view}

    @r.patch("/episodes/{episode_id}")
    def patch_episode(episode_id: int, body: DataIn, _user: dict = Depends(write)):
        row = service.episodes.update(episode_id, {key: value for key, value in body.data.items() if key in {"title", "aired_on", "notes", "status"}})
        if not row:
            raise Problem(404, "épisode introuvable")
        return {"ok": True, "episode": row}

    @r.get("/episodes/{episode_id}/transcript")
    def transcript(episode_id: int, _user: dict = Depends(read)):
        doc = service.transcript(episode_id)
        if doc is None:
            raise Problem(404, "pas de transcription")
        return {"ok": True, **doc}

    @r.post("/episodes/{episode_id}/transcribe", status_code=202)
    def transcribe(episode_id: int, user: dict = Depends(write)):
        try:
            return {"ok": True, "job": service.request_transcription(episode_id, actor=user["id"])}
        except KeyError as exc:
            raise Problem(404, str(exc)) from exc

    @r.post("/episodes/{episode_id}/analyze", status_code=202)
    def analyze(episode_id: int, _user: dict = Depends(write)):
        if not service.episodes.get(episode_id):
            raise Problem(404, "épisode introuvable")
        return {"ok": True, "job": k.jobs.submit("shows.analyze", {"episode_id": episode_id}, P_USER, dedupe=True)}

    @r.get("/episodes/{episode_id}/suggest")
    def suggest(episode_id: int, _user: dict = Depends(read)):
        return {"ok": True, "suggestions": service.suggest_segments(episode_id)}

    @r.post("/episodes/{episode_id}/segments", status_code=201)
    def add_segment(episode_id: int, body: SegmentIn, _user: dict = Depends(write)):
        try:
            return {"ok": True, "segment": service.save_segment(episode_id, {key: value for key, value in body.model_dump().items() if value is not None})}
        except KeyError as exc:
            raise Problem(404, str(exc)) from exc
        except ValueError as exc:
            raise Problem(400, str(exc)) from exc

    @r.patch("/segments/{segment_id}")
    def patch_segment(segment_id: int, body: SegmentPatch, _user: dict = Depends(write)):
        row = service.segments.get(segment_id)
        if not row:
            raise Problem(404, "séquence introuvable")
        try:
            return {"ok": True, "segment": service.save_segment(int(row["episode_id"]), {key: value for key, value in body.model_dump().items() if value is not None}, segment_id)}
        except ValueError as exc:
            raise Problem(400, str(exc)) from exc

    @r.delete("/segments/{segment_id}")
    def delete_segment(segment_id: int, _user: dict = Depends(write)):
        if not service.delete_segment(segment_id):
            raise Problem(404, "séquence introuvable")
        return {"ok": True}

    @r.post("/segments/validate")
    def validate(body: ValidateIn, user: dict = Depends(write)):
        return {"ok": True, "action": k.actions.perform("shows.validate_segment", [str(i) for i in body.ids], {"valid": body.valid}, actor_id=user["id"], label="Séquence validée" if body.valid else "Validation retirée")}

    @r.post("/episodes/{episode_id}/bornes", status_code=201)
    def bornes(episode_id: int, _user: dict = Depends(write)):
        try:
            return {"ok": True, "bornes_path": service.write_bornes(episode_id)}
        except KeyError as exc:
            raise Problem(404, str(exc)) from exc

    @r.get("/podcasts")
    def podcasts(show_id: int | None = None, status: str | None = None, _user: dict = Depends(read)):
        where, params = [], []
        if show_id:
            where.append("show_id = %s")
            params.append(show_id)
        if status:
            where.append("status = %s")
            params.append(status)
        rows = service.podcasts.list(" AND ".join(where), params, 300)
        for row in rows:
            ok, reason = service.publishable(row)
            row["publishable"] = ok
            row["blocked_reason"] = reason
        return {"ok": True, "podcasts": rows}

    @r.post("/podcasts", status_code=201)
    def create_podcast(body: PodcastIn, user: dict = Depends(write)):
        try:
            return {"ok": True, "podcast": service.create_podcast(body.model_dump(), actor=user["id"])}
        except KeyError as exc:
            raise Problem(404, str(exc)) from exc
        except ValueError as exc:
            raise Problem(400, str(exc)) from exc

    @r.get("/podcasts/{podcast_id}")
    def podcast(podcast_id: int, _user: dict = Depends(read)):
        row = service.podcasts.get(podcast_id)
        if not row:
            raise Problem(404, "podcast introuvable")
        ok, reason = service.publishable(row)
        return {"ok": True, "podcast": {**row, "publishable": ok, "blocked_reason": reason}, "links": k.links.of("podcast", podcast_id), "publications": _publications(service, podcast_id)}

    @r.patch("/podcasts/{podcast_id}")
    def patch_podcast(podcast_id: int, body: PodcastPatch, _user: dict = Depends(write)):
        data = {key: value for key, value in body.model_dump().items() if value is not None}
        if data.get("rights_status") and data["rights_status"] not in {"unknown", "ok", "blocked"}:
            raise Problem(400, "statut de droits inconnu")
        row = service.update_podcast(podcast_id, data)
        if not row:
            raise Problem(404, "podcast introuvable")
        return {"ok": True, "podcast": row}

    @r.post("/podcasts/{podcast_id}/export", status_code=202)
    def export(podcast_id: int, body: ExportIn, user: dict = Depends(write)):
        if body.format not in {"mp3", "wav"}:
            raise Problem(400, "format mp3 ou wav")
        try:
            return {"ok": True, "job": service.request_export(podcast_id, actor=user["id"], fmt=body.format)}
        except KeyError as exc:
            raise Problem(404, str(exc)) from exc

    return r


def _episode_counts(service: ShowsService) -> list[dict[str, Any]]:
    from regie.kernel import db

    with db.connect(service.kernel.dsn) as conn:
        return db.fetch_all(conn, "SELECT show_id, COUNT(*) AS n FROM episodes WHERE org_id = %s GROUP BY show_id", (service.kernel.org_id,))


def _publications(service: ShowsService, podcast_id: int) -> list[dict[str, Any]]:
    from regie.kernel import db

    with db.connect(service.kernel.dsn) as conn:
        rows = db.fetch_all(conn, "SELECT * FROM publications WHERE entity_kind = 'podcast' AND entity_id = %s ORDER BY id", (str(podcast_id),))
    return [db.jsonable(r) or {} for r in rows]

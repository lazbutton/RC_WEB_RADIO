from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from regie.kernel.api import Problem, require
from regie.kernel.jobs import P_USER
from regie.modules.publish.service import TARGETS, PublishService


class PublishIn(BaseModel):
    targets: list[str] = list(TARGETS)
    wp_status: str = "draft"


def build_router(service: PublishService) -> APIRouter:
    r = APIRouter(prefix="/api/v1/publish", tags=["publish"])
    read = require("publish", "read")
    write = require("publish", "write")
    k = service.kernel

    @r.get("/status")
    def status(_user: dict = Depends(read)):
        return {"ok": True, "wordpress": {"configured": service.wordpress.configured(), "state": k.connectors.state.get("wordpress")}, "edge": {"configured": service.edge.configured(), "provider": service.edge.provider, "public_url": service.edge.public_url, "state": k.connectors.state.get("edge"), "last_at": k.setting("publish.last_edge_at") or None}, "snippets": service.snippets(), "publications": service.publications(limit=30)}

    @r.post("/podcasts/{podcast_id}")
    def publish_podcast(podcast_id: int, body: PublishIn, user: dict = Depends(write)):
        targets = [t for t in body.targets if t in TARGETS]
        if not targets:
            raise Problem(400, "aucune cible")
        if body.wp_status not in {"draft", "publish"}:
            raise Problem(400, "statut WordPress : draft ou publish")
        try:
            action = k.actions.perform("shows.publish", [str(podcast_id)], {"targets": targets, "wp_status": body.wp_status}, actor_id=user["id"])
        except PermissionError as exc:
            raise Problem(409, str(exc)) from exc
        if action.get("status") == "failed":
            raise Problem(409, action.get("error") or "publication refusée")
        return {"ok": True, "action": action, "publications": service.publications("podcast", str(podcast_id))}

    @r.post("/run", status_code=202)
    def run(_user: dict = Depends(write)):
        return {"ok": True, "job": k.jobs.submit("publish.run", {}, P_USER, dedupe=True)}

    @r.post("/regenerate", status_code=202)
    def regenerate(_user: dict = Depends(write)):
        if not service.edge.configured():
            raise Problem(503, "bord public non configuré (EDGE_PROVIDER, EDGE_TOKEN, EDGE_PROJECT).")
        return {"ok": True, "job": k.jobs.submit("publish.edge", {}, P_USER, dedupe=True)}

    @r.get("/preview/{block}", response_class=HTMLResponse)
    def preview(block: str, _user: dict = Depends(read)):
        files = service.build_files()
        key = f"{block}/index.html"
        if key not in files:
            raise Problem(404, "bloc inconnu")
        return HTMLResponse(files[key].decode("utf-8"), headers={"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src https: data:; media-src https: http:"})

    @r.get("/files")
    def files(_user: dict = Depends(read)):
        built = service.build_files()
        return {"ok": True, "files": sorted(built), "bytes": sum(len(v) for v in built.values())}

    @r.get("/rss/{show_slug}.xml")
    def rss(show_slug: str, request: Request):
        """Flux local (jeton de flux ou session). Le flux public est celui du bord."""
        xml = service.rss(show_slug)
        if xml is None:
            return JSONResponse({"error": "émission inconnue ou flux désactivé"}, status_code=404)
        return Response(xml, media_type="application/rss+xml; charset=utf-8")

    return r

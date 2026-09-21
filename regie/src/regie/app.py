from __future__ import annotations

import hmac
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from regie import __version__
from regie.config import Settings, get_settings
from regie.kernel.api import COOKIE, build_router
from regie.kernel.core import Kernel
from regie.kernel.observability import new_request_id, request_id, setup_logging
from regie.modules import load_modules

log = logging.getLogger("regie.app")
ROOT = Path(__file__).parent
SPA_DIR = ROOT / "static" / "app"
PUBLIC_PATHS = {"/health", "/api/v1/auth/login", "/brand.json", "/openapi.json"}
MUTATING = {"POST", "PUT", "PATCH", "DELETE"}
CSP = (
    "default-src 'self'; img-src 'self' data: blob: https:; media-src 'self' blob:; "
    "style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
)


def _public(path: str) -> bool:
    if path in PUBLIC_PATHS or path.startswith("/assets/"):
        return True
    return not path.startswith("/api/")


def create_app(settings: Settings | None = None, *, start_workers: bool | None = None, kernel: Kernel | None = None) -> FastAPI:
    """Fabrique de l'application. Le noyau et les modules sont construits ici (les routes doivent exister avant le catch-all SPA)."""
    settings = settings or get_settings()
    setup_logging(settings.regie_log_json)
    if start_workers is not None:
        settings = settings.model_copy(update={"regie_run_workers": start_workers})
    kernel = kernel or Kernel(settings)
    kernel.settings = settings
    modules = kernel.modules or load_modules(kernel)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        kernel.bootstrap()
        kernel.start()
        log.info("Régie %s démarrée (%s modules)", __version__, len(kernel.modules))
        try:
            yield
        finally:
            kernel.stop()

    app = FastAPI(title="Régie", version=__version__, docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.kernel = kernel
    for module in modules.values():
        router = getattr(module, "router", None)
        if router is not None:
            app.include_router(router)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        rid = request.headers.get("x-request-id") or new_request_id()
        token = request_id.set(rid)
        started = time.perf_counter()
        kernel: Kernel | None = getattr(request.app.state, "kernel", None)
        request.state.user = None
        path = request.url.path
        try:
            if kernel and not _public(path):
                user = kernel.auth.resolve(request.cookies.get(COOKIE, ""))
                if not user and _feed_ok(kernel, request, path):
                    return await call_next(request)
                if not user:
                    return _json({"error": "auth"}, 401, rid)
                request.state.user = user
                if request.method in MUTATING:
                    if request.headers.get("x-regie") != "1":
                        return _json({"error": "en-tête X-Regie manquant"}, 403, rid)
                    origin = request.headers.get("origin")
                    if origin and request.headers.get("host") and origin.split("//", 1)[-1] != request.headers.get("host"):
                        return _json({"error": "origine refusée"}, 403, rid)
            response = await call_next(request)
        except Exception:
            log.exception("requête %s %s", request.method, path)
            return _json({"error": "erreur interne", "request_id": rid}, 500, rid)
        finally:
            request_id.reset(token)
        ms = (time.perf_counter() - started) * 1000
        if kernel and path.startswith("/api/"):
            route = request.scope.get("route")
            name = getattr(route, "path", path)
            kernel.metrics.observe("http_ms", ms, route=name)
            kernel.metrics.inc("http_requests", route=name, status=str(response.status_code))
        response.headers["X-Request-Id"] = rid
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Frame-Options"] = "DENY"
        if not path.startswith("/api/"):
            response.headers["Content-Security-Policy"] = CSP
        return response

    app.include_router(build_router())

    @app.get("/health")
    def health(request: Request):
        kernel: Kernel | None = getattr(request.app.state, "kernel", None)
        if kernel is None:
            return {"ok": False}
        try:
            snap = kernel.jobs.snapshot()
        except Exception as exc:  # base injoignable
            return JSONResponse({"ok": False, "error": str(exc)[:200]}, status_code=503)
        return {"ok": True, "version": __version__, "late_jobs": snap["late"], "dead_jobs": snap["dead"]}

    @app.get("/brand.json")
    def brand():
        path = ROOT / "brand.json"
        if path.is_file():
            return FileResponse(path)
        return {"suite": {"id": "button", "name": "BUTTON", "short": "BTN"}, "labels": {"regie": {"title": "Régie"}}, "infra": {"smbShare": "BUTTON-Media", "partnerCode": ""}, "apps": {}}

    assets = SPA_DIR / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        if full_path.startswith("api/"):
            return JSONResponse({"error": "not found"}, status_code=404)
        if full_path:
            candidate = (SPA_DIR / full_path).resolve()
            try:
                candidate.relative_to(SPA_DIR.resolve())
            except ValueError:
                return JSONResponse({"error": "not found"}, status_code=404)
            if candidate.is_file():
                return FileResponse(candidate)
        index = SPA_DIR / "index.html"
        if not index.is_file():
            return HTMLResponse("<!doctype html><title>Régie</title><p>Interface absente : construire ui/ puis copier dist/ dans static/app.</p>", status_code=503)
        return FileResponse(index)

    return app


def _json(payload: dict[str, Any], status: int, rid: str) -> JSONResponse:
    return JSONResponse(payload, status_code=status, headers={"X-Request-Id": rid})


def _feed_ok(kernel: Kernel, request: Request, path: str) -> bool:
    """Les flux (Atom, Markdown) s'abonnent avec un jeton porteur dédié, jamais avec la session."""
    if not (path.endswith("/feed.md") or path.endswith("/feed.atom") or ("/rss/" in path and path.endswith(".xml"))):
        return False
    header = request.headers.get("authorization", "")
    token = header[7:].strip() if header.lower().startswith("bearer ") else request.query_params.get("key", "")
    expected = kernel.setting("feed_token")
    return bool(token and expected and hmac.compare_digest(token, expected))

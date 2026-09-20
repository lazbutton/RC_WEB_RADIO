"""Serve the React admin SPA from console/dist when present."""

from pathlib import Path

from quart import Quart, current_app, send_from_directory


def console_dist() -> Path | None:
    configured = current_app.config.get("CONSOLE_DIST")
    if configured:
        path = Path(str(configured))
    else:
        path = Path(__file__).resolve().parents[3] / "console" / "dist"
    if path.is_dir() and (path / "index.html").is_file():
        return path
    return None


def register(app: Quart) -> None:
    @app.get("/antenne")
    @app.get("/horloges")
    @app.get("/semaine")
    @app.get("/conducteur")
    @app.get("/categories")
    @app.get("/habillage")
    @app.get("/comptes")
    async def spa_index():
        dist = console_dist()
        if dist is None:
            return "Console dist introuvable (npm run build).", 404
        return await send_from_directory(dist, "index.html")

    @app.get("/assets/<path:filename>")
    async def spa_assets(filename: str):
        dist = console_dist()
        if dist is None:
            return "Not found", 404
        return await send_from_directory(dist / "assets", filename)

    @app.get("/logo.svg")
    async def spa_logo():
        dist = console_dist()
        if dist is None:
            return "Not found", 404
        return await send_from_directory(dist, "logo.svg")

"""Module Émissions : grille, épisodes, bornes, podcasts."""

from __future__ import annotations

from regie.kernel.core import Kernel


def setup(kernel: Kernel):
    from regie.modules.shows.api import build_router
    from regie.modules.shows.service import ShowsService

    service = ShowsService(kernel)
    service.router = build_router(service)  # type: ignore[attr-defined]
    return service

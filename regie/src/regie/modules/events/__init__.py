"""Module Événements : cache Outlive et couverture éditoriale."""

from __future__ import annotations

from regie.kernel.core import Kernel


def setup(kernel: Kernel, connector=None):
    from regie.modules.events.api import build_router
    from regie.modules.events.service import EventsService

    service = EventsService(kernel, connector=connector)
    service.router = build_router(service)  # type: ignore[attr-defined]
    return service

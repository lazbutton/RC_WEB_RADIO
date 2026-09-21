"""Module Agenda : Google bidirectionnel, ICS en repli."""

from __future__ import annotations

from regie.kernel.core import Kernel


def setup(kernel: Kernel, connector=None):
    from regie.modules.calendar.api import build_router
    from regie.modules.calendar.service import CalendarService

    service = CalendarService(kernel, connector=connector)
    service.router = build_router(service)  # type: ignore[attr-defined]
    return service

"""Module Vie de la radio : invités, réservations, volontaires, partenariats, conducteur, valorisation, écoute."""

from __future__ import annotations

from regie.kernel.core import Kernel


def setup(kernel: Kernel):
    from regie.modules.radio.api import build_router
    from regie.modules.radio.service import RadioService

    service = RadioService(kernel)
    service.router = build_router(service)  # type: ignore[attr-defined]
    return service

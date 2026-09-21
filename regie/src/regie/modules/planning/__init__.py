"""Module Planning : tâches, commentaires, disponibilités, tableau de bord."""

from __future__ import annotations

from regie.kernel.core import Kernel


def setup(kernel: Kernel):
    from regie.modules.planning.api import build_router
    from regie.modules.planning.service import PlanningService

    service = PlanningService(kernel)
    service.router = build_router(service)  # type: ignore[attr-defined]
    return service

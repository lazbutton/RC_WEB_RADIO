"""Module Publier : WordPress, RSS, blocs iframe vers le bord public."""

from __future__ import annotations

from regie.kernel.core import Kernel


def setup(kernel: Kernel, wordpress=None, edge=None):
    from regie.modules.publish.api import build_router
    from regie.modules.publish.service import PublishService

    service = PublishService(kernel, wordpress=wordpress, edge=edge)
    service.router = build_router(service)  # type: ignore[attr-defined]
    return service

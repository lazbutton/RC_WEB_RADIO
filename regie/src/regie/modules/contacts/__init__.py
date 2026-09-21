"""Module Contacts : personnes, structures, lieux, affiliations, interactions."""

from __future__ import annotations

from regie.kernel.core import Kernel


def setup(kernel: Kernel):
    from regie.modules.contacts.api import build_router
    from regie.modules.contacts.service import ContactsService

    service = ContactsService(kernel)
    service.router = build_router(service)  # type: ignore[attr-defined]
    return service

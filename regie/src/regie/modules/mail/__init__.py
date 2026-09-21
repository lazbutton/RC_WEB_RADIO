"""Module Mails : Inbox Zero sur les contrats du noyau."""

from __future__ import annotations

from regie.kernel.core import Kernel


def setup(kernel: Kernel, session=None):
    from regie.modules.mail.api import build_router
    from regie.modules.mail.service import MailService

    service = MailService(kernel, session=session)
    service.router = build_router(service)  # type: ignore[attr-defined]
    return service

"""Stable application errors that do not depend on Quart or Werkzeug."""

from __future__ import annotations


class DomainError(Exception):
    status_code = 400
    code = "domain_error"

    def __init__(self, message: str, *, field: str | None = None):
        super().__init__(message)
        self.message = message
        self.field = field


class DomainValidationError(DomainError):
    code = "validation_error"


class DomainConflict(DomainError):  # noqa: N818
    status_code = 409
    code = "conflict"


class DomainNotFound(DomainError):  # noqa: N818
    status_code = 404
    code = "not_found"

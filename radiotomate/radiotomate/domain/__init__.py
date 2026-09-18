"""Framework-independent domain rules and errors."""

from radiotomate.domain.errors import (
    DomainConflict,
    DomainError,
    DomainNotFound,
    DomainValidationError,
)

__all__ = [
    "DomainConflict",
    "DomainError",
    "DomainNotFound",
    "DomainValidationError",
]

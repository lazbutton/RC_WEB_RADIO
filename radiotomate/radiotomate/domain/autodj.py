"""Pure invariants for clocks and weekly Auto-DJ slots."""

from __future__ import annotations

from radiotomate.domain.errors import DomainConflict, DomainValidationError
from radiotomate.enums import PositionKind, SyncMode, WhenMode

MINUTES_PER_DAY = 24 * 60


def validate_slot_timing(day_of_week: int, minute: int) -> None:
    if not 0 <= day_of_week <= 6:
        raise DomainValidationError("day_of_week must be 0-6", field="day_of_week")
    if not 0 <= minute < MINUTES_PER_DAY:
        raise DomainValidationError("minute must be 0-1439", field="minute")


def protect_midnight_slot(
    *,
    previous_day: int,
    previous_minute: int,
    next_day: int,
    next_minute: int,
) -> None:
    if previous_minute == 0 and (
        previous_day != next_day or previous_minute != next_minute
    ):
        raise DomainConflict("The midnight slot cannot be moved.", field="minute")


def validate_position(data: dict, when_mode: str) -> tuple[str, int | None, str | None]:
    kind = str(data.get("kind") or "")
    if kind not in {item.value for item in PositionKind}:
        raise DomainValidationError(f"invalid kind: {kind}", field="kind")
    if when_mode == WhenMode.SEQUENTIAL.value:
        return kind, None, None
    try:
        minute = int(data.get("minute"))
    except (TypeError, ValueError) as exc:
        raise DomainValidationError(
            "anchor needs minute",
            field="minute",
        ) from exc
    if not 0 <= minute <= 59:
        raise DomainValidationError("minute must be 0-59", field="minute")
    sync = str(data.get("sync") or SyncMode.DURE.value)
    if sync not in {item.value for item in SyncMode}:
        raise DomainValidationError(f"invalid sync: {sync}", field="sync")
    return kind, minute, sync


def validate_clock_rows(
    motif: object,
    anchors: object,
) -> tuple[list[dict], list[dict]]:
    if not isinstance(motif, list) or not isinstance(anchors, list):
        raise DomainValidationError(
            "motif and anchors must be lists",
            field="motif",
        )
    if not motif:
        raise DomainValidationError(
            "a clock needs a sequential motif",
            field="motif",
        )
    if not all(isinstance(row, dict) for row in [*motif, *anchors]):
        raise DomainValidationError("invalid clock item", field="motif")
    minutes = [validate_position(row, WhenMode.ANCHORED.value)[1] for row in anchors]
    if len(minutes) != len(set(minutes)):
        raise DomainValidationError("duplicate anchor minute", field="anchors")
    return motif, anchors


def require_expected_version(current: int, expected: object) -> None:
    if expected is None:
        return
    try:
        parsed = int(expected)
    except (TypeError, ValueError) as exc:
        raise DomainValidationError("invalid version", field="version") from exc
    if parsed != current:
        raise DomainConflict(
            f"Object changed since version {parsed}; current version is {current}.",
            field="version",
        )

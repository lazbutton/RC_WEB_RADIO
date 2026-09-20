"""Invariants for live show overlays (weekly grid + Antenne session)."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from radiotomate.domain.errors import DomainConflict, DomainValidationError

PARIS = ZoneInfo("Europe/Paris")


def now_paris(now: datetime | None = None) -> datetime:
    if now is None:
        return datetime.now(PARIS)
    if now.tzinfo is None:
        return now.replace(tzinfo=PARIS)
    return now.astimezone(PARIS)

MINUTES_PER_DAY = 24 * 60
SCOPE_WEEKLY = "weekly"
SCOPE_SESSION = "session"
STATE_WAITING = "waiting"
STATE_ON_AIR = "on_air"


def is_harbor_source(source: object) -> bool:
    text = str(source or "").strip().lower()
    if not text:
        return False
    return "stream" in text or "harbor" in text


def minute_of_day(now: datetime) -> int:
    local = now_paris(now)
    return local.hour * 60 + local.minute


def ranges_overlap(start_a: int, end_a: int, start_b: int, end_b: int) -> bool:
    return start_a < end_b and start_b < end_a


def weekly_contains(
    now: datetime,
    day_of_week: int,
    start_minute: int,
    end_minute: int,
) -> bool:
    local = now_paris(now)
    if local.weekday() != day_of_week:
        return False
    current = minute_of_day(local)
    return start_minute <= current < end_minute


def validate_title_artist(title: object, artist: object) -> tuple[str, str]:
    name = str(title or "").strip()
    phrase = str(artist or "").strip()
    if not name:
        raise DomainValidationError("Le nom de l'emission est requis.", field="title")
    if not phrase:
        raise DomainValidationError("La phrase artiste est requise.", field="artist")
    return name, phrase


def validate_weekly_window(
    day_of_week: object,
    start_minute: object,
    end_minute: object,
) -> tuple[int, int, int]:
    try:
        day = int(day_of_week)
        start = int(start_minute)
        end = int(end_minute)
    except (TypeError, ValueError) as exc:
        raise DomainValidationError(
            "Jour, debut et fin sont requis.",
            field="start_minute",
        ) from exc
    if not 0 <= day <= 6:
        raise DomainValidationError(
            "Le jour doit etre compris entre 0 et 6.",
            field="day_of_week",
        )
    if not 0 <= start < MINUTES_PER_DAY:
        raise DomainValidationError(
            "Le debut doit etre entre 00:00 et 23:59.",
            field="start_minute",
        )
    if not 1 <= end <= MINUTES_PER_DAY:
        raise DomainValidationError(
            "La fin doit etre entre 00:01 et 24:00.",
            field="end_minute",
        )
    if start >= end:
        raise DomainValidationError(
            "La fin doit etre apres le debut.",
            field="end_minute",
        )
    return day, start, end


def reject_weekly_overlap(
    existing: list[tuple[int, int, int, int]],
    *,
    day_of_week: int,
    start_minute: int,
    end_minute: int,
    ignore_id: int | None = None,
) -> None:
    for row_id, day, start, end in existing:
        if ignore_id is not None and row_id == ignore_id:
            continue
        if day != day_of_week:
            continue
        if ranges_overlap(start_minute, end_minute, start, end):
            raise DomainConflict(
                "Ce creneau chevauche une autre emission.",
                field="start_minute",
            )


def weekly_end_at(now: datetime, end_minute: int) -> datetime:
    local = now_paris(now).replace(second=0, microsecond=0)
    start_of_day = local.replace(hour=0, minute=0)
    return start_of_day + timedelta(minutes=end_minute)


def weekly_start_at(now: datetime, day_of_week: int, start_minute: int) -> datetime:
    local = now_paris(now).replace(second=0, microsecond=0)
    start_of_today = local.replace(hour=0, minute=0)
    days_ahead = (day_of_week - local.weekday()) % 7
    current = minute_of_day(local)
    if days_ahead == 0 and start_minute <= current:
        days_ahead = 7
    return start_of_today + timedelta(days=days_ahead, minutes=start_minute)

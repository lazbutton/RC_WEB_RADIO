"""Bounded history: purge closed rundown rows, acknowledged commands, orphan
programming versions and (after export) old metadata logs.

Nothing here touches the future: only rows whose life is over are removed, and
the as-run log is appended to a JSONL archive before deletion so nothing is lost.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, select

from radiotomate.enums import CommandStatus, RundownStatus
from radiotomate.models import (
    MetadataLog,
    PlayoutCommand,
    ProgrammingVersion,
    RundownItem,
)
from radiotomate.quart import ShutdownError, or_shutdown

_log = logging.getLogger(__name__)

DEFAULT_INTERVAL_SECONDS = 3600.0
MIN_INTERVAL_SECONDS = 60.0
DEFAULT_RUNDOWN_DAYS = 7
DEFAULT_COMMANDS_HOURS = 24
DEFAULT_METADATA_DAYS = 90
EXPORT_DIR = "exports"
BATCH = 500

CLOSED_RUNDOWN_STATUSES = {
    RundownStatus.PLAYED.value,
    RundownStatus.SKIPPED.value,
    RundownStatus.REPLACED.value,
    RundownStatus.FAILED.value,
}
CLOSED_COMMAND_STATUSES = {
    CommandStatus.ACKNOWLEDGED.value,
    CommandStatus.FAILED.value,
    CommandStatus.EXPIRED.value,
}


def _positive(raw, default: float) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def settings_from_config(config) -> dict:
    return {
        "enabled": bool(config.get("RETENTION_ENABLED", True)),
        "interval": max(
            MIN_INTERVAL_SECONDS,
            _positive(config.get("RETENTION_INTERVAL"), DEFAULT_INTERVAL_SECONDS),
        ),
        "rundown_days": _positive(
            config.get("RETENTION_RUNDOWN_DAYS"), DEFAULT_RUNDOWN_DAYS
        ),
        "commands_hours": _positive(
            config.get("RETENTION_COMMANDS_HOURS"), DEFAULT_COMMANDS_HOURS
        ),
        "metadata_days": _positive(
            config.get("RETENTION_METADATA_DAYS"), DEFAULT_METADATA_DAYS
        ),
    }


def _log_row(row: MetadataLog) -> dict:
    return {
        "id": row.id,
        "on_air": row.on_air.isoformat() if row.on_air else None,
        "source": row.source,
        "source_url": row.source_url,
        "cart_id": row.cart_id,
        "rundown_item_id": row.rundown_item_id,
        "playout_command_id": row.playout_command_id,
        "artist": row.artist,
        "title": row.title,
        "album": row.album,
        "extra": dict(row.extra or {}),
    }


def _append_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


async def purge_commands(session, *, now: datetime, hours: float) -> int:
    cutoff = now - timedelta(hours=hours)
    ids = list(
        await session.scalars(
            select(PlayoutCommand.id)
            .where(PlayoutCommand.status.in_(CLOSED_COMMAND_STATUSES))
            .where(PlayoutCommand.created < cutoff)
        )
    )
    if not ids:
        return 0
    # Keep the as-run link consistent: the log row loses its command pointer.
    await session.execute(
        MetadataLog.__table__.update()
        .where(MetadataLog.playout_command_id.in_(ids))
        .values(playout_command_id=None)
    )
    await session.execute(delete(PlayoutCommand).where(PlayoutCommand.id.in_(ids)))
    return len(ids)


async def purge_rundown(session, *, now: datetime, days: float) -> int:
    cutoff = now - timedelta(days=days)
    ids = list(
        await session.scalars(
            select(RundownItem.id)
            .where(RundownItem.status.in_(CLOSED_RUNDOWN_STATUSES))
            .where(RundownItem.planned_at < cutoff)
        )
    )
    if not ids:
        return 0
    await session.execute(
        MetadataLog.__table__.update()
        .where(MetadataLog.rundown_item_id.in_(ids))
        .values(rundown_item_id=None)
    )
    await session.execute(
        delete(PlayoutCommand).where(PlayoutCommand.rundown_item_id.in_(ids))
    )
    await session.execute(delete(RundownItem).where(RundownItem.id.in_(ids)))
    return len(ids)


async def purge_orphan_versions(session, *, now: datetime) -> int:
    """Drop fingerprints no rundown row references any more (older than a day)."""
    referenced = select(RundownItem.programming_version_id).distinct()
    cutoff = now - timedelta(days=1)
    ids = list(
        await session.scalars(
            select(ProgrammingVersion.id)
            .where(ProgrammingVersion.id.not_in(referenced))
            .where(ProgrammingVersion.created < cutoff)
        )
    )
    if not ids:
        return 0
    await session.execute(
        delete(ProgrammingVersion).where(ProgrammingVersion.id.in_(ids))
    )
    return len(ids)


async def archive_metadata(
    session, *, now: datetime, days: float, export_dir: Path
) -> int:
    cutoff = now - timedelta(days=days)
    total = 0
    while True:
        rows = list(
            await session.scalars(
                select(MetadataLog)
                .where(MetadataLog.on_air < cutoff)
                .order_by(MetadataLog.on_air)
                .limit(BATCH)
            )
        )
        if not rows:
            return total
        by_month: dict[str, list[dict]] = {}
        for row in rows:
            key = row.on_air.strftime("%Y-%m") if row.on_air else "unknown"
            by_month.setdefault(key, []).append(_log_row(row))
        for month, payload in by_month.items():
            await asyncio.to_thread(
                _append_jsonl, export_dir / f"metadata_log-{month}.jsonl", payload
            )
        await session.execute(
            delete(MetadataLog).where(MetadataLog.id.in_([row.id for row in rows]))
        )
        await session.flush()
        total += len(rows)


async def run_retention(app, now: datetime | None = None) -> dict:
    conf = settings_from_config(app.config)
    now = now or datetime.now()
    db = app.extensions["sqlalchemy"]
    export_dir = Path(app.config["DATA_ROOT"]) / EXPORT_DIR
    async with db.session() as session:
        # Archive first so the JSONL keeps the rundown/command links intact.
        archived = await archive_metadata(
            session, now=now, days=conf["metadata_days"], export_dir=export_dir
        )
        commands = await purge_commands(session, now=now, hours=conf["commands_hours"])
        rundown = await purge_rundown(session, now=now, days=conf["rundown_days"])
        versions = await purge_orphan_versions(session, now=now)
        await session.commit()
    result = {
        "commands": commands,
        "rundown_items": rundown,
        "programming_versions": versions,
        "metadata_archived": archived,
    }
    if any(result.values()):
        _log.info("retention: %s", result)
    return result


async def loop(app) -> None:
    conf = settings_from_config(app.config)
    while True:
        try:
            await or_shutdown(asyncio.sleep(conf["interval"]))
            await run_retention(app)
        except (ShutdownError, asyncio.CancelledError):
            break
        except Exception:
            _log.exception("retention loop crashed")

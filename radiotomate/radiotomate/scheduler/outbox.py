"""Transactional outbox for idempotent playout commands."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import select

from radiotomate.enums import CommandStatus, PlayoutAction, RundownStatus
from radiotomate.models import PlayoutCommand, RundownItem

if TYPE_CHECKING:
    from sqlalchemy.orm import Session as ormSession

    from radiotomate.scheduler.playout import PlayoutGateway, PlayoutResult

_log = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
RETRY_DELAY = timedelta(seconds=2)
COMMAND_TTL = timedelta(minutes=15)


def _now() -> datetime:
    return datetime.now()


async def enqueue_command(  # noqa: PLR0913
    session: ormSession,
    *,
    action: str,
    queue: str | None,
    payload: dict,
    item: RundownItem | None = None,
    idempotency_key: str | None = None,
) -> PlayoutCommand:
    key = idempotency_key or _default_key(action, queue, item, payload)
    existing = await PlayoutCommand.from_idempotency_key(session, key)
    if existing is not None:
        return existing
    command = PlayoutCommand(
        id=str(uuid4()),
        rundown_item_id=item.id if item is not None else None,
        action=action,
        queue=queue,
        payload=payload,
        idempotency_key=key,
        status=CommandStatus.PENDING.value,
        available_at=_now(),
    )
    session.add(command)
    await session.flush()
    if item is not None and item.status in {
        RundownStatus.PLANNED.value,
        RundownStatus.RESCUE.value,
        RundownStatus.RESERVED.value,
    }:
        item.status = RundownStatus.PENDING_PUSH.value
        item.reserved_at = item.reserved_at or _now()
    return command


async def dispatch_command(
    session: ormSession,
    command: PlayoutCommand,
    gateway: PlayoutGateway,
) -> PlayoutResult:
    if command.status == CommandStatus.ACKNOWLEDGED.value:
        from radiotomate.scheduler.playout import PlayoutResult

        return PlayoutResult(ok=True, status_code=200, payload=command.response)
    if command.status == CommandStatus.EXPIRED.value:
        from radiotomate.scheduler.playout import PlayoutResult

        return PlayoutResult(ok=False, error="command expired")

    command.status = CommandStatus.SENDING.value
    command.attempts += 1
    command.sent_at = command.sent_at or _now()
    command.lease_until = _now() + RETRY_DELAY
    await session.flush()

    result = await _send(command, gateway)
    if result.ok:
        command.status = CommandStatus.ACKNOWLEDGED.value
        command.acknowledged_at = _now()
        command.response = _as_json(result.payload)
        command.last_error = None
        await _mark_item(session, command, success=True)
        return result

    command.last_error = result.error
    command.response = _as_json(result.payload)
    if command.attempts >= MAX_ATTEMPTS or not result.transient:
        command.status = CommandStatus.FAILED.value
        await _mark_item(session, command, success=False, reason=result.error)
    else:
        command.status = CommandStatus.PENDING.value
        command.available_at = _now() + RETRY_DELAY
    return result


async def dispatch_pending_commands(
    session: ormSession,
    gateway: PlayoutGateway,
    *,
    limit: int = 8,
) -> list[PlayoutCommand]:
    await expire_stale_commands(session)
    now = _now()
    rows = list(
        await session.scalars(
            select(PlayoutCommand)
            .where(
                PlayoutCommand.status == CommandStatus.PENDING.value,
                PlayoutCommand.available_at <= now,
            )
            .order_by(PlayoutCommand.created)
            .limit(limit)
        )
    )
    for command in rows:
        await dispatch_command(session, command, gateway)
    return rows


async def expire_stale_commands(session: ormSession) -> int:
    cutoff = _now() - COMMAND_TTL
    rows = list(
        await session.scalars(
            select(PlayoutCommand).where(
                PlayoutCommand.status.in_(
                    [CommandStatus.PENDING.value, CommandStatus.SENDING.value]
                ),
                PlayoutCommand.created < cutoff,
            )
        )
    )
    for command in rows:
        command.status = CommandStatus.EXPIRED.value
        command.last_error = command.last_error or "expired before acknowledgement"
        await _mark_item(
            session,
            command,
            success=False,
            reason="commande expirée",
        )
    return len(rows)


async def _send(command: PlayoutCommand, gateway: PlayoutGateway) -> PlayoutResult:
    if command.action == PlayoutAction.QUEUE.value:
        return await gateway.queue(command.queue or "autodj", command.payload)
    if command.action == PlayoutAction.SKIP.value:
        return await gateway.skip(command.payload or None)
    if command.action == PlayoutAction.RELAY_START.value:
        return await gateway.relay_start(str(command.payload.get("url") or ""))
    if command.action == PlayoutAction.RELAY_STOP.value:
        return await gateway.skip({"relay": 1})
    from radiotomate.scheduler.playout import PlayoutResult

    return PlayoutResult(ok=False, error=f"unknown action {command.action}")


async def _mark_item(
    session: ormSession,
    command: PlayoutCommand,
    *,
    success: bool,
    reason: str | None = None,
) -> None:
    if not command.rundown_item_id:
        return
    item = await RundownItem.from_id(session, command.rundown_item_id)
    if item is None:
        return
    now = _now()
    if success:
        item.status = RundownStatus.ACCEPTED.value
        item.accepted_at = now
        item.sent_at = item.sent_at or command.sent_at
        item.queued_at = now
        if command.action == PlayoutAction.QUEUE.value:
            item.status = RundownStatus.IN_QUEUE.value
    else:
        item.status = RundownStatus.FAILED.value
        item.reason = reason
        item.closed_at = now


def _default_key(
    action: str,
    queue: str | None,
    item: RundownItem | None,
    payload: dict,
) -> str:
    if item is not None:
        return f"{item.id}:{action}:{queue or '-'}"
    sound_id = payload.get("radiotomate_sound_id") or payload.get("beets_id") or ""
    return f"loose:{action}:{queue or '-'}:{sound_id}:{uuid4().hex[:8]}"


def _as_json(payload) -> dict | None:
    if payload is None:
        return None
    if isinstance(payload, dict):
        return payload
    return {"raw": str(payload)}

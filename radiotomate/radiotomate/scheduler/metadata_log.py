import asyncio
import logging

import httpx
from quart import Blueprint, current_app, g, request

from radiotomate.auth import token_required
from radiotomate.models import MetadataLog
from radiotomate.scheduler.metrics import runtime_metrics

_log = logging.getLogger(__name__)

blueprint = Blueprint("metadata_log", __name__)

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_TIMEOUT_SECONDS = 2.0
DEFAULT_BACKOFF_SECONDS = (0.2, 0.5)


def _retry_config() -> tuple[int, float, list[float]]:
    raw = current_app.config.get("RELAY_METADATA_RETRY", {})
    try:
        configured_attempts = raw.get("max_attempts", DEFAULT_MAX_ATTEMPTS)
        max_attempts = max(1, min(5, int(configured_attempts)))
    except (AttributeError, TypeError, ValueError):
        max_attempts = DEFAULT_MAX_ATTEMPTS
    try:
        configured_timeout = raw.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
        timeout = max(0.1, min(30.0, float(configured_timeout)))
    except (AttributeError, TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT_SECONDS
    try:
        configured_backoff = raw.get("backoff_seconds", DEFAULT_BACKOFF_SECONDS)
        backoff = [
            max(0.0, min(5.0, float(value)))
            for value in configured_backoff
        ]
    except (AttributeError, TypeError, ValueError):
        backoff = list(DEFAULT_BACKOFF_SECONDS)
    return max_attempts, timeout, backoff


def _retryable_status(status_code: int) -> bool:
    return status_code in {408, 425, 429} or status_code >= 500


async def relay_metadata(raw_md: dict) -> None:
    max_attempts, timeout, backoff = _retry_config()
    async with httpx.AsyncClient(timeout=timeout) as client:
        for target in current_app.config["RELAY_METADATA_TO"]:
            if not isinstance(target, dict):
                _log.warning("incorrect format for relay_to block: %s", str(target))
                runtime_metrics.record_metadata_relay("invalid")
                continue
            url = target.get("url")
            if not isinstance(url, str) or not url:
                _log.warning("metadata relay target has no URL")
                runtime_metrics.record_metadata_relay("invalid")
                continue
            data = {**(target.get("add_field") or {}), **raw_md}
            add_header = target.get("add_header")
            for attempt in range(max_attempts):
                retry = False
                try:
                    response = await client.post(url, data=data, headers=add_header)
                    if 200 <= response.status_code < 300:
                        runtime_metrics.record_metadata_relay("success")
                        break
                    retry = _retryable_status(response.status_code)
                    if not retry:
                        _log.error(
                            "Metadata relay to %s rejected with HTTP %d",
                            url,
                            response.status_code,
                        )
                except httpx.TransportError as exc:
                    retry = True
                    _log.warning(
                        "Metadata relay attempt %d/%d to %s failed: %s",
                        attempt + 1,
                        max_attempts,
                        url,
                        exc,
                    )

                last_attempt = attempt + 1 >= max_attempts
                if not retry or last_attempt:
                    runtime_metrics.record_metadata_relay("failure")
                    if retry and last_attempt:
                        _log.error(
                            "Metadata relay to %s failed after %d attempts",
                            url,
                            max_attempts,
                        )
                    break

                runtime_metrics.record_metadata_relay("retry")
                delay = backoff[min(attempt, len(backoff) - 1)] if backoff else 0.0
                if delay:
                    await asyncio.sleep(delay)


@blueprint.post("/metadata_log")
@token_required
async def post_metadata_log():
    raw_md = await request.get_json()

    if current_app.config["RELAY_METADATA_TO"]:
        # POST a copy of the dict because from_playout modifies it
        current_app.add_background_task(relay_metadata, dict(raw_md))

    md = await MetadataLog.from_playout(g.dbsession, raw_md)
    g.dbsession.add(md)
    from radiotomate.scheduler.execution import reconcile_as_run

    await reconcile_as_run(g.dbsession, md)
    await g.dbsession.commit()

    return "", 200

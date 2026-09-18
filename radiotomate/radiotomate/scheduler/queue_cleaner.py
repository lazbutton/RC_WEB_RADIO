"""Periodic Liquidsoap queue cleaner driven by the scheduler process."""

from __future__ import annotations

import asyncio
import logging

from radiotomate.quart import ShutdownError, or_shutdown
from radiotomate.scheduler.playout import gateway_for

_log = logging.getLogger(__name__)

DEFAULT_MAX_AGE_SECONDS = 600.0
MIN_MAX_AGE_SECONDS = 30.0
MAX_MAX_AGE_SECONDS = 7200.0
DEFAULT_INTERVAL_SECONDS = 60.0
MIN_INTERVAL_SECONDS = 15.0
MAX_INTERVAL_SECONDS = 3600.0


def clamp_max_age(raw) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = DEFAULT_MAX_AGE_SECONDS
    return max(MIN_MAX_AGE_SECONDS, min(MAX_MAX_AGE_SECONDS, value))


def clamp_interval(raw) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = DEFAULT_INTERVAL_SECONDS
    return max(MIN_INTERVAL_SECONDS, min(MAX_INTERVAL_SECONDS, value))


async def run_clean(app) -> dict:
    age = clamp_max_age(app.config.get("QUEUE_CLEAN_MAX_AGE"))
    gateway = gateway_for(app.config["PLAYOUT_CLIENT"])
    result = await gateway.clean_queues(age)
    if not result.ok:
        _log.warning(
            "queue cleaner failed after %s attempt(s): %s",
            result.attempts,
            result.error,
        )
        return {"ok": False, "error": "playout_unavailable"}
    payload = result.payload if isinstance(result.payload, dict) else {}
    return {"ok": True, "queues": payload}


async def loop(app) -> None:
    interval = clamp_interval(app.config.get("QUEUE_CLEAN_INTERVAL"))
    while True:
        try:
            await or_shutdown(asyncio.sleep(interval))
            await run_clean(app)
        except (ShutdownError, asyncio.CancelledError):
            break
        except Exception:
            _log.exception("queue cleaner loop crashed")

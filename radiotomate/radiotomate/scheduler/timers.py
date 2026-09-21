"""One-shot in-process timers (the only thing left of APScheduler).

Used for ``Cart.max_duration``: after a cart sound or a relay is pushed, the
playout is asked to skip it when the allowed time is over. Recurring carts are
the clock's business (dayparts, anchors, pads), so a plain ``asyncio`` sleep
per deadline is enough — no data store, no alpha dependency.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

_log = logging.getLogger(__name__)


class Timers:
    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task] = {}
        self._due: dict[str, datetime] = {}

    def schedule(
        self,
        key: str,
        when: datetime,
        action: Callable[[], Awaitable[object]],
    ) -> None:
        """Arm (or re-arm) ``key`` to run ``action`` at ``when``."""
        self.cancel(key)
        delay = max(0.0, (when - datetime.now()).total_seconds())
        self._due[key] = when

        async def _fire() -> None:
            try:
                await asyncio.sleep(delay)
                self._due.pop(key, None)
                await action()
            except asyncio.CancelledError:
                raise
            except Exception:
                _log.exception("timer %s failed", key)
            finally:
                self._tasks.pop(key, None)
                self._due.pop(key, None)

        self._tasks[key] = asyncio.get_running_loop().create_task(_fire())

    def cancel(self, key: str) -> bool:
        task = self._tasks.pop(key, None)
        self._due.pop(key, None)
        if task is None:
            return False
        task.cancel()
        return True

    def next_fire(self, key: str) -> datetime | None:
        return self._due.get(key)

    def cancel_all(self) -> None:
        for key in list(self._tasks):
            self.cancel(key)

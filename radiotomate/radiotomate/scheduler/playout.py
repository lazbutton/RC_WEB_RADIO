"""Single adapter for Liquidsoap HTTP commands."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

from radiotomate.scheduler.metrics import runtime_metrics

_log = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 5.0
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BACKOFF_SECONDS = (0.1, 0.3)


@dataclass
class PlayoutResult:
    ok: bool
    status_code: int = 0
    payload: Any = None
    error: str | None = None
    attempts: int = 1
    transient: bool = False
    response: httpx.Response | None = field(default=None, repr=False)


class PlayoutError(Exception):
    def __init__(self, result: PlayoutResult):
        super().__init__(result.error or "playout request failed")
        self.result = result


class PlayoutGateway:
    """Timeouts, retries and a single call site for queue / skip / relay."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        backoff: tuple[float, ...] = DEFAULT_BACKOFF_SECONDS,
    ):
        self.client = client
        self.timeout = timeout
        self.max_attempts = max(1, min(5, max_attempts))
        self.backoff = backoff

    async def queue(self, queue: str, payload: dict) -> PlayoutResult:
        result = await self._request("POST", f"/queue/{queue}", json=payload)
        runtime_metrics.record_push(queue, "ok" if result.ok else "error")
        return result

    async def skip(self, params: dict | None = None) -> PlayoutResult:
        return await self._request("DELETE", "/live", params=params)

    async def relay_start(self, url: str) -> PlayoutResult:
        return await self._request("POST", "/relay", params={"url": url})

    async def version(self) -> PlayoutResult:
        return await self._request("GET", "/version")

    async def clean_queues(self, max_age_seconds: float) -> PlayoutResult:
        result = await self._request(
            "POST",
            "/queue/clean",
            json={"max_age_seconds": max_age_seconds},
        )
        payload = result.payload if isinstance(result.payload, dict) else {}
        for queue in ("autodj", "jingles", "carts"):
            info = payload.get(queue)
            if not isinstance(info, dict):
                continue
            try:
                removed = int(info.get("removed", 0))
            except (TypeError, ValueError):
                removed = 0
            runtime_metrics.record_queue_clean(queue, removed)
        return result

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict | None = None,
        params: dict | None = None,
    ) -> PlayoutResult:
        last = PlayoutResult(ok=False, error="no attempt")
        for attempt in range(1, self.max_attempts + 1):
            try:
                response = await self._call(
                    method,
                    path,
                    json=json,
                    params=params,
                )
            except httpx.TransportError as exc:
                last = PlayoutResult(
                    ok=False,
                    error=str(exc),
                    attempts=attempt,
                    transient=True,
                )
                _log.warning(
                    "Playout %s %s attempt %d/%d failed: %s",
                    method,
                    path,
                    attempt,
                    self.max_attempts,
                    exc,
                )
            else:
                body: Any
                try:
                    body = response.json()
                except ValueError:
                    body = response.text
                ok = 200 <= response.status_code < 300
                transient = _retryable_status(response.status_code)
                last = PlayoutResult(
                    ok=ok,
                    status_code=response.status_code,
                    payload=body,
                    error=None if ok else str(body),
                    attempts=attempt,
                    transient=transient,
                    response=response,
                )
                if ok:
                    return last
                _log.error(
                    "Playout %s %s rejected with HTTP %s: %s",
                    method,
                    path,
                    response.status_code,
                    body,
                )
                if not transient:
                    return last

            if attempt >= self.max_attempts:
                return last
            delay = self.backoff[min(attempt - 1, len(self.backoff) - 1)]
            if delay:
                await asyncio.sleep(delay)
        return last

    async def _call(
        self,
        method: str,
        path: str,
        *,
        json: dict | None,
        params: dict | None,
    ) -> httpx.Response:
        kwargs: dict = {}
        if json is not None:
            kwargs["json"] = json
        if params is not None:
            kwargs["params"] = params
        if method == "POST":
            return await self.client.post(path, **kwargs)
        if method == "DELETE":
            return await self.client.delete(path, **kwargs)
        if method == "GET":
            return await self.client.get(path, **kwargs)
        raise ValueError(f"unsupported playout method: {method}")


def _retryable_status(status_code: int) -> bool:
    return status_code in {408, 425, 429} or status_code >= 500


def gateway_for(client: httpx.AsyncClient) -> PlayoutGateway:
    return PlayoutGateway(client)

from __future__ import annotations

from datetime import datetime
from email.utils import parsedate_to_datetime

import httpx

from oracle.clock import Clock
from oracle.net.errors import RequestFailed
from oracle.net.gate import SourceGate

BACKOFF_BASE_S = 2.0
BACKOFF_CAP_S = 60.0


class RateLimitedClient:
    """Async HTTP through a SourceGate. Every retry goes back through the gate, so a
    backoff or Retry-After delays every process using that source, not just this call.

    Returns any non-429, non-5xx response as-is (403 and 404 included) for the caller
    to interpret; the gate still counts 403s toward the breaker.
    """

    def __init__(
        self,
        gate: SourceGate,
        clock: Clock,
        user_agent: str,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_s: float = 20.0,
    ) -> None:
        self._gate = gate
        self._clock = clock
        self._http = httpx.AsyncClient(
            headers={"User-Agent": user_agent},
            transport=transport,
            timeout=timeout_s,
            follow_redirects=True,
        )

    async def get(
        self,
        source: str,
        url: str,
        *,
        params: dict | None = None,
        headers: dict | None = None,
    ) -> httpx.Response:
        retries = self._gate.config(source).max_retries
        for attempt in range(retries + 1):
            last = attempt == retries
            await self._gate.acquire(source)
            try:
                resp = await self._http.get(url, params=params, headers=headers)
            except httpx.TransportError as exc:
                self._gate.record_transport_error(source)
                if last:
                    raise RequestFailed(source, None, f"{type(exc).__name__}: {exc}") from exc
                self._gate.defer(source, _backoff(attempt))
                continue

            self._gate.record(source, resp.status_code)
            if resp.status_code == 429:
                delay = self._retry_after(resp) or _backoff(attempt)
            elif resp.status_code >= 500:
                delay = _backoff(attempt)
            else:
                return resp
            if last:
                raise RequestFailed(source, resp.status_code, f"HTTP {resp.status_code}")
            self._gate.defer(source, delay)
        raise AssertionError("unreachable")

    async def aclose(self) -> None:
        await self._http.aclose()

    def _retry_after(self, resp: httpx.Response) -> float | None:
        value = resp.headers.get("Retry-After")
        if not value:
            return None
        try:
            return max(0.0, float(value))
        except ValueError:
            pass
        try:
            when: datetime = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        return max(0.0, (when - self._clock.utcnow()).total_seconds())


def _backoff(attempt: int) -> float:
    return min(BACKOFF_CAP_S, BACKOFF_BASE_S * 2**attempt)

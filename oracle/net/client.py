from __future__ import annotations

import logging
import re
from datetime import timezone
from email.utils import parsedate_to_datetime

import httpx

from oracle.clock import Clock
from oracle.net.errors import RequestFailed
from oracle.net.gate import SourceGate

BACKOFF_BASE_S = 2.0
BACKOFF_CAP_S = 60.0
# Longer waits are not slept through in-call: the caller gets a failure now, and the
# source's shared slot still moves back by the full wait so no process ignores it.
MAX_IN_CALL_WAIT_S = 120.0

# httpx logs every request URL at INFO, and paid sources take their key in the query.
_SECRET_PARAM = re.compile(r"((?:api_token|apikey|api_key|token)=)[^&\s\"']+", re.IGNORECASE)


class _RedactSecrets(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = _SECRET_PARAM.sub(r"\1***", message)
        if redacted != message:
            record.msg, record.args = redacted, ()
        return True


logging.getLogger("httpx").addFilter(_RedactSecrets())


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
        max_bytes: int | None = None,
    ) -> httpx.Response:
        """With `max_bytes`, reading stops there (an arbitrary page can be any size);
        `extensions["oracle_truncated"]` says whether it was cut."""
        retries = self._gate.config(source).max_retries
        for attempt in range(retries + 1):
            last = attempt == retries
            await self._gate.acquire(source)
            try:
                resp = await (self._http.get(url, params=params, headers=headers) if max_bytes is None
                              else self._get_capped(url, params, headers, max_bytes))
            except httpx.TransportError as exc:
                self._gate.record_transport_error(source)
                if last:
                    raise RequestFailed(source, None, f"{type(exc).__name__}: {exc}") from exc
                self._gate.defer(source, _backoff(attempt))
                continue

            self._gate.record(source, resp.status_code)
            if resp.status_code != 429 and resp.status_code < 500:
                return resp
            asked = self._retry_after(resp)
            delay = _backoff(attempt) if asked is None else asked
            self._gate.defer(source, delay)
            if delay > MAX_IN_CALL_WAIT_S:
                raise RequestFailed(
                    source, resp.status_code, f"HTTP {resp.status_code}, Retry-After {delay:.0f}s"
                )
            if last:
                raise RequestFailed(source, resp.status_code, f"HTTP {resp.status_code}")
        raise AssertionError("unreachable")

    async def _get_capped(self, url: str, params: dict | None, headers: dict | None,
                          max_bytes: int) -> httpx.Response:
        async with self._http.stream("GET", url, params=params, headers=headers) as resp:
            body = bytearray()
            async for chunk in resp.aiter_bytes():
                body += chunk
                if len(body) >= max_bytes:
                    break
            truncated = len(body) > max_bytes or (len(body) == max_bytes and not resp.is_stream_consumed)
        # The body is already decompressed and may be cut, so its encoding and length no
        # longer describe it.
        kept = {k: v for k, v in resp.headers.items() if k.lower() not in ("content-encoding", "content-length")}
        return httpx.Response(resp.status_code, headers=kept, content=bytes(body[:max_bytes]),
                              request=resp.request, extensions={"oracle_truncated": truncated})

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
            when = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if when.tzinfo is None:  # "-0000" means UTC with no stated origin (RFC 5322)
            when = when.replace(tzinfo=timezone.utc)
        return max(0.0, (when - self._clock.utcnow()).total_seconds())


def _backoff(attempt: int) -> float:
    return min(BACKOFF_CAP_S, BACKOFF_BASE_S * 2**attempt)

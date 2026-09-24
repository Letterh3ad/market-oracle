import logging
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import httpx
import pytest

from oracle.clock import SystemClock
from oracle.config import SourceConfig
from oracle.net.client import RateLimitedClient
from oracle.net.errors import CircuitOpen, RequestFailed
from oracle.net.gate import SourceGate
from tests.fakes.clock import FakeClock

START = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)
URL = "https://example.test/x"


@pytest.fixture
def clock():
    return FakeClock(start_utc=START)


def _client(conn, clock, responses, seen=None, max_retries=3):
    """responses: a list of int statuses, (status, headers) pairs, or exceptions."""
    queue = list(responses)

    def handler(request):
        if seen is not None:
            seen.append(request)
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        status, headers = item if isinstance(item, tuple) else (item, {})
        return httpx.Response(status, headers=headers, text="body")

    gate = SourceGate(conn, clock, {"src": SourceConfig(rate_per_second=10.0, max_retries=max_retries)})
    return RateLimitedClient(gate, clock, user_agent="oracle-test/1", transport=httpx.MockTransport(handler))


async def test_a_success_is_returned_with_our_user_agent(conn, clock):
    seen = []
    client = _client(conn, clock, [200], seen)
    resp = await client.get("src", URL, params={"q": "a"})
    assert resp.status_code == 200
    assert seen[0].headers["user-agent"] == "oracle-test/1"
    assert seen[0].url.params["q"] == "a"


async def test_retry_after_seconds_is_honoured(conn, clock):
    client = _client(conn, clock, [(429, {"Retry-After": "7"}), 200])
    resp = await client.get("src", URL)
    assert resp.status_code == 200
    assert clock.sleeps == [pytest.approx(7.0)]


async def test_retry_after_http_date_is_honoured(conn, clock):
    when = format_datetime(START + timedelta(seconds=30), usegmt=True)
    client = _client(conn, clock, [(429, {"Retry-After": when}), 200])
    await client.get("src", URL)
    assert clock.sleeps == [pytest.approx(30.0)]


async def test_5xx_backs_off_exponentially_then_succeeds(conn, clock):
    client = _client(conn, clock, [503, 502, 200])
    resp = await client.get("src", URL)
    assert resp.status_code == 200
    assert clock.sleeps == [pytest.approx(2.0), pytest.approx(4.0)]


async def test_persistent_5xx_gives_up_after_max_retries(conn, clock):
    seen = []
    client = _client(conn, clock, [503, 503, 503], seen, max_retries=2)
    with pytest.raises(RequestFailed) as err:
        await client.get("src", URL)
    assert err.value.status == 503
    assert len(seen) == 3


async def test_persistent_429_ends_at_the_breaker(conn, clock):
    client = _client(conn, clock, [429, 429, 429, 429])
    with pytest.raises(CircuitOpen):
        await client.get("src", URL)


async def test_a_404_is_returned_without_retry(conn, clock):
    seen = []
    client = _client(conn, clock, [404], seen)
    assert (await client.get("src", URL)).status_code == 404
    assert len(seen) == 1


async def test_403s_are_returned_but_a_run_opens_the_breaker(conn, clock):
    client = _client(conn, clock, [403, 403, 403])
    for _ in range(3):
        assert (await client.get("src", URL)).status_code == 403
    with pytest.raises(CircuitOpen, match="403"):
        await client.get("src", URL)


async def test_403s_a_caller_reads_as_one_pages_refusal_never_open_the_breaker(conn, clock):
    client = _client(conn, clock, [403, 403, 403, 403, 200])
    for _ in range(4):
        assert (await client.get("src", URL, count_403=False)).status_code == 403
    assert (await client.get("src", URL, count_403=False)).status_code == 200


async def test_a_transport_error_is_retried(conn, clock):
    client = _client(conn, clock, [httpx.ConnectError("down"), 200])
    assert (await client.get("src", URL)).status_code == 200
    assert clock.sleeps == [pytest.approx(2.0)]


async def test_persistent_transport_errors_raise_request_failed(conn, clock):
    client = _client(conn, clock, [httpx.ConnectError("down")] * 2, max_retries=1)
    with pytest.raises(RequestFailed) as err:
        await client.get("src", URL)
    assert err.value.status is None


async def test_retry_after_zero_retries_at_the_next_normal_slot(conn, clock):
    client = _client(conn, clock, [(429, {"Retry-After": "0"}), 200])
    await client.get("src", URL)
    assert clock.sleeps == [pytest.approx(0.1)]


async def test_retry_after_on_a_503_is_honoured(conn, clock):
    client = _client(conn, clock, [(503, {"Retry-After": "9"}), 200])
    await client.get("src", URL)
    assert clock.sleeps == [pytest.approx(9.0)]


async def test_retry_after_with_an_unknown_zone_is_read_as_utc(conn, clock):
    client = _client(conn, clock, [(429, {"Retry-After": "Tue, 22 Sep 2026 12:00:30 -0000"}), 200])
    await client.get("src", URL)
    assert clock.sleeps == [pytest.approx(30.0)]


async def test_a_huge_retry_after_fails_now_but_still_holds_back_the_source(conn, clock):
    seen = []
    client = _client(conn, clock, [(429, {"Retry-After": "3600"}), 200], seen)
    with pytest.raises(RequestFailed, match="3600"):
        await client.get("src", URL)
    assert len(seen) == 1
    assert clock.sleeps == []
    await client.get("src", URL)
    assert clock.sleeps == [pytest.approx(3600.0)]


async def test_secret_query_parameters_never_reach_the_http_log(conn, caplog):
    gate = SourceGate(conn, SystemClock(), {"eodhd": SourceConfig(rate_per_second=1000)})
    client = RateLimitedClient(gate, SystemClock(), "t", transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    with caplog.at_level(logging.INFO, logger="httpx"):
        await client.get("eodhd", "https://eodhd.com/api/eod/AAPL.US", params={"api_token": "sk-secret-1", "from": "2026"})
    await client.aclose()
    assert "HTTP Request" in caplog.text
    assert "sk-secret-1" not in caplog.text and "api_token=***" in caplog.text


async def test_a_body_past_max_bytes_is_cut_there_and_marked(conn, clock):
    gate = SourceGate(conn, clock, {"src": SourceConfig(rate_per_second=10.0)})

    def handler(request):
        return httpx.Response(200, content=b"x" * 5000)

    client = RateLimitedClient(gate, clock, user_agent="t", transport=httpx.MockTransport(handler))
    resp = await client.get("src", URL, max_bytes=1000)
    assert resp.content == b"x" * 1000 and resp.extensions["oracle_truncated"] is True
    whole = await client.get("src", URL, max_bytes=10_000)
    assert len(whole.content) == 5000 and whole.extensions["oracle_truncated"] is False


async def test_a_compressed_body_is_capped_after_decompressing(conn, clock):
    import gzip

    gate = SourceGate(conn, clock, {"src": SourceConfig(rate_per_second=10.0)})

    def handler(request):
        return httpx.Response(200, content=gzip.compress(b"<p>" + b"y" * 3000), headers={"Content-Encoding": "gzip"})

    client = RateLimitedClient(gate, clock, user_agent="t", transport=httpx.MockTransport(handler))
    resp = await client.get("src", URL, max_bytes=100)
    assert resp.text == "<p>" + "y" * 97


async def test_a_body_of_exactly_max_bytes_is_not_marked_cut(conn, clock):
    gate = SourceGate(conn, clock, {"src": SourceConfig(rate_per_second=10.0)})
    client = RateLimitedClient(gate, clock, user_agent="t",
                               transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"x" * 1000)))
    resp = await client.get("src", URL, max_bytes=1000)
    assert len(resp.content) == 1000 and resp.extensions["oracle_truncated"] is False


async def test_a_post_sends_its_form_through_the_gate_with_the_same_retries(conn, clock):
    seen = []
    client = _client(conn, clock, [503, 200], seen)
    resp = await client.post("src", URL, data={"f.req": "[1]"}, headers={"Cookie": "a=b"})
    assert resp.status_code == 200 and clock.sleeps == [pytest.approx(2.0)]
    assert [r.method for r in seen] == ["POST", "POST"]
    assert seen[1].content == b"f.req=%5B1%5D" and seen[1].headers["cookie"] == "a=b"
    assert seen[1].headers["user-agent"] == "oracle-test/1"


async def test_a_post_transport_error_is_retried_then_raises_request_failed(conn, clock):
    seen = []
    client = _client(conn, clock, [httpx.ConnectError("down")] * 3, seen, max_retries=2)
    with pytest.raises(RequestFailed):
        await client.post("src", URL, data={"a": "b"})
    assert len(seen) == 3


async def test_a_post_body_past_max_bytes_is_cut_there(conn, clock):
    gate = SourceGate(conn, clock, {"src": SourceConfig(rate_per_second=10.0)})
    client = RateLimitedClient(gate, clock, user_agent="t",
                               transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"x" * 50)))
    resp = await client.post("src", URL, data={"a": "b"}, max_bytes=10)
    assert resp.content == b"x" * 10 and resp.extensions["oracle_truncated"] is True


@pytest.mark.parametrize("failure", [429, 503, httpx.ConnectError("down")])
async def test_a_call_off_the_breaker_is_tried_once_counts_nothing_but_still_holds_back_the_source(
        conn, clock, failure):
    seen = []
    client = _client(conn, clock, [failure, 200], seen)
    with pytest.raises(RequestFailed):
        await client.post("src", URL, data={"a": "b"}, breaker=False)
    assert len(seen) == 1
    status = SourceGate(conn, clock, {"src": SourceConfig(rate_per_second=10.0)}).status("src")
    assert status.open_until is None and status.next_slot_in_s > 0.1
    row = conn.execute("SELECT run_429, run_5xx FROM source_state WHERE source = 'src'").fetchone()
    assert tuple(row) == (0, 0)

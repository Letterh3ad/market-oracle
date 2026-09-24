import httpx

from oracle.config import SourceConfig
from oracle.net.client import RateLimitedClient
from oracle.net.gate import SourceGate
from oracle.services.research_tools.shelf import fetch
from tests.fakes.clock import FakeClock
from tests.net.test_client import START


async def test_a_form_post_keeps_every_option_a_get_takes(conn):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(302, headers={"Location": "https://example.test/next"}, content=b"x" * 50)

    clock = FakeClock(start_utc=START)
    gate = SourceGate(conn, clock, {"src": SourceConfig(rate_per_second=10.0)})
    client = RateLimitedClient(gate, clock, "t", transport=httpx.MockTransport(handler))
    resp = await fetch(client, "src", "it", "https://example.test/x", params={"q": "a"}, form={"f": "b"},
                       max_bytes=10, follow_redirects=False, accept=(302,))
    assert [(r.method, r.url.params["q"], r.content) for r in seen] == [("POST", "a", b"f=b")]
    assert resp.content == b"x" * 10 and resp.extensions["oracle_truncated"]

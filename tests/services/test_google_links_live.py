"""Live Google News stubs decoded through the app's gate (run with -m network): catches Google
changing the stub format or its decoder."""

import pytest

from oracle.config import Config
from oracle.research.google_links import decode_offline, stub_id
from oracle.research.news import GOOGLE_NEWS, google_news_params, parse_google_news
from oracle.services.context import open_context
from oracle.services.http import open_client
from oracle.services.research_tools.google_links import decode_online
from oracle.services.research_tools.reading import from_outlet
from oracle.services.research_tools.shelf import fetch

pytestmark = pytest.mark.network
STUBS = 4


async def test_live_stubs_decode_to_their_outlets_articles(tmp_path):
    with open_context(Config(db_path=tmp_path / "o.db"), None) as ctx:
        async with open_client(ctx) as client:
            feed = await fetch(client, "google_news", "news search", GOOGLE_NEWS,
                               params=google_news_params("Apple earnings"))
            articles = [a for a in parse_google_news(feed.text) if stub_id(a.url)][:STUBS]
            assert articles, "the feed gave no stubs"
            decoded = [(a, decode_offline(stub_id(a.url)) or await decode_online(client, stub_id(a.url)))
                       for a in articles]
    assert all(from_outlet(url, a.site, a.outlet) for a, url in decoded), decoded

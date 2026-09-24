"""A newer Google News stub decoded by Google's own decoder: two calls through the
`google_news` gate, the stub's page for a signature, then the decoder itself."""

from __future__ import annotations

from oracle.net.client import RateLimitedClient
from oracle.research.google_links import DECODER, STUB_PAGE, decoded_url, decoder_form, signature
from oracle.research.news import ENGLISH_EDITION
from oracle.research.tools import ToolError
from oracle.services.research_tools.shelf import fetch

LABEL = "Google's link decoder"
# The stub page is ~600 KB with the signature near its end.
STUB_PAGE_BYTES = 2_000_000
# Declines Google's cookie consent, which otherwise redirects every European request to it.
NO_CONSENT = {"Cookie": "SOCS=CAI"}


async def decode_online(client: RateLimitedClient, stub: str) -> str:
    """The article's address, or ToolError (Unavailable when passing) saying why not."""
    hl, gl, ceid = ENGLISH_EDITION
    page = await fetch(client, "google_news", LABEL, STUB_PAGE.format(stub), params={"hl": hl, "gl": gl, "ceid": ceid},
                       headers={**NO_CONSENT, "Accept-Encoding": "identity"}, max_bytes=STUB_PAGE_BYTES,
                       follow_redirects=False)
    signed = signature(page.text)
    if signed is None:
        raise ToolError(f"{LABEL} found no signature on the link's page")
    answer = await fetch(client, "google_news", LABEL, DECODER, headers=NO_CONSENT, form=decoder_form(stub, *signed))
    url = decoded_url(answer.text)
    if url is None:
        raise ToolError(f"{LABEL} gave no address")
    return url

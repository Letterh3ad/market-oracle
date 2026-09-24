"""The filings tool's helpers for the reading stage."""

from oracle.services.research_tools.filings import press_exhibit


class RecordingClient:
    def __init__(self) -> None:
        self.requests: list[tuple] = []

    async def get(self, *args, **kwargs):
        self.requests.append(args)
        raise AssertionError("no request was expected")


async def test_a_filing_that_ended_up_off_sec_gov_is_not_asked_for_an_exhibit():
    client = RecordingClient()
    document = "https://evil.test/Archives/edgar/data/320193/000032019326000090/d.htm"
    assert await press_exhibit(client, "Faye faye@example.com", document) is None
    assert client.requests == []

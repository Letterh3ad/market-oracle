import pytest

from oracle.market.sources import AllFailed, NoData, SourceFailed, first_success


class Source:
    def __init__(self, name, outcome):
        self.name, self.outcome = name, outcome


async def call(source):
    if isinstance(source.outcome, Exception):
        raise source.outcome
    return source.outcome


@pytest.mark.parametrize(("outcomes", "no_data"), [
    ([NoData("no data"), NoData("not found")], True),
    ([NoData("no data"), []], True),  # an empty first fetch is no data too
    ([NoData("no data"), SourceFailed("HTTP 429", 429)], False),
    ([SourceFailed("timed out")], False),
    ([NoData("no data"), ValueError("bad json")], False),
    ([], False),  # no source enabled says nothing about the listing
])
async def test_all_failed_says_whether_every_source_answered_no_data(outcomes, no_data):
    sources = [Source(f"s{i}", o) for i, o in enumerate(outcomes)]
    with pytest.raises(AllFailed) as err:
        await first_success(sources, call, empty_is_no_data=True)
    assert err.value.no_data is no_data

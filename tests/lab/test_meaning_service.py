"""Meaning search over the literature index (research-lab 04): stored definition embeddings, cosine top 5,
a labelled keyword fallback when Ollama is down, and the near-duplicate-of-a-FAIL flag."""

from datetime import datetime, timezone

import pytest

from oracle.config import Config
from oracle.services import literature_service as lab
from oracle.services import meaning_service as meaning
from oracle.services.context import open_context
from oracle.services.errors import InvalidQuery
from tests.fakes.clock import FakeClock
from tests.fakes.embed import FakeEmbed

NOW = datetime(2026, 10, 4, 1, 0, tzinfo=timezone.utc)
YEARLY = "buy stocks near their yearly high"


@pytest.fixture
def ctx(tmp_path):
    config = Config(db_path=tmp_path / "live.db", research_db_path=tmp_path / "research.db")
    with open_context(config, None, FakeClock(NOW)) as c:
        yield c


def test_meaning_finds_the_52_week_high_entry_by_cosine_top_5(ctx):
    found = meaning.similar(ctx, YEARLY, embed=FakeEmbed())
    assert found.mode == "meaning" and "fake-embed" in found.note
    assert len(found.hits) == 5 and found.hits[0].ref == "T-15" and found.hits[0].score > 0.9
    assert [h.score for h in found.hits] == sorted((h.score for h in found.hits), reverse=True)
    assert found.hits[0].status == "new" and found.hits[0].name == "52-week-high proximity"


def test_definitions_are_embedded_once_and_again_only_when_new_or_changed(ctx):
    embed = FakeEmbed()
    meaning.similar(ctx, YEARLY, embed=embed)
    (docs, as_query), (query, q_flag) = embed.calls
    assert len(docs) == 205 and not as_query and query == [YEARLY] and q_flag
    assert any(d.startswith("52-week-high proximity") and "stocks near their 52-week high" in d for d in docs)
    embed.calls.clear()
    meaning.similar(ctx, "anything", embed=embed)
    assert embed.calls == [(["anything"], True)]
    embed.calls.clear()
    lab.record(ctx, title="A new paper", new_method="Annual peak chasing", definition="buy at the annual peak")
    found = meaning.similar(ctx, YEARLY, embed=embed)
    assert len(embed.calls[0][0]) == 1 and embed.calls[0][0][0].startswith("Annual peak chasing")
    assert {"T-15", "M-001"} <= {h.ref for h in found.hits[:3]}


def test_another_model_never_reads_the_first_ones_vectors(ctx):
    meaning.similar(ctx, YEARLY, embed=FakeEmbed())
    other = FakeEmbed(model="other-embed")
    meaning.similar(ctx, YEARLY, embed=other)
    assert len(other.calls[0][0]) == 205


def test_ollama_down_falls_back_to_keywords_and_says_so(ctx):
    found = meaning.similar(ctx, YEARLY, embed=FakeEmbed(down=True))
    assert found.mode == "keyword"
    assert "keyword" in found.note and "not reachable" in found.note
    assert len(found.hits) == 5 and "T-15" in {h.ref for h in found.hits}


def test_tests_never_reach_a_real_ollama(ctx):
    assert meaning.similar(ctx, YEARLY).mode == "keyword"


def test_an_empty_query_is_refused(ctx):
    with pytest.raises(InvalidQuery):
        meaning.similar(ctx, "  ", embed=FakeEmbed())


def test_a_near_duplicate_of_a_failed_method_is_flagged_and_others_are_not(ctx):
    check = meaning.near_fail(ctx, "own the market over its 200-day average, cash otherwise", embed=FakeEmbed())
    near = check.hit
    assert near is not None and near.ref == "T-62" and near.mode == "meaning" and near.score >= meaning.NEAR["meaning"]
    assert near.label.startswith("T-62 Trend overlay (10-month MA) (meaning ")
    assert check.mode == "meaning" and "fake-embed" in check.note
    # T-15 is as near in meaning but untested, so nothing is flagged.
    clear = meaning.near_fail(ctx, YEARLY, embed=FakeEmbed())
    assert clear.hit is None and clear.mode == "meaning"


def test_the_keyword_fallback_flags_only_a_close_wording_of_a_fail_and_says_it_ran_on_keywords(ctx):
    down = FakeEmbed(down=True)
    check = meaning.near_fail(ctx, "Hold the index above its 10-month MA, cash below", embed=down)
    assert check.hit is not None and check.hit.ref == "T-62" and check.hit.mode == "keyword"
    missed = meaning.near_fail(ctx, "own the market over its 200-day average, cash otherwise", embed=down)
    assert missed.hit is None and missed.mode == "keyword" and "not reachable" in missed.note

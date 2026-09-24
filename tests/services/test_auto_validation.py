"""The validation layer an auto question's companies pass before they are added unattended:
each check passing and failing."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from oracle.config import Config
from oracle.research.llm import ModelError
from oracle.research.news import Article
from oracle.services.auto_validation import Validator
from oracle.services.context import open_context
from oracle.services.discovery_service import remove_discovered
from oracle.services.prices_service import update_listing
from oracle.services.universe_service import refresh_universe
from oracle.services.watch_service import watch
from oracle.store.articles import ArticleRepo
from oracle.store.discoveries import DISCOVERED_LIST
from oracle.store.questions import ShortlistItem
from oracle.store.universe import UniverseRepo
from tests.fakes.clock import FakeClock
from tests.fakes.financedatabase import FakeFinanceDatabase, equity
from tests.fakes.gdelt import FakeGdelt
from tests.fakes.google_news import FakeGoogleNews
from tests.fakes.llm import ScriptedModel
from tests.fakes.web import FakeWeb
from tests.fakes.web_search import FakePages
from tests.fakes.yahoo import FakeYahoo, bar

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)
QUESTION = "which companies gain from grid batteries?"
FLUENCE_ISIN = "US34379V1035"
UNIVERSE = [
    equity("FLNC", "Fluence Energy, Inc.", isin=FLUENCE_ISIN, currency="USD", market_cap="Mid Cap"),
    equity("FLNC.DE", "Fluence Energy, Inc.", exchange="GER", isin=FLUENCE_ISIN, currency="EUR"),
    equity("BIDU", "Baidu, Inc. ADR", currency="USD", market_cap="Large Cap"),
    equity("005930.KS", "Samsung Electronics Co., Ltd.", "South Korea", "KSC", isin="KR7005930003", currency="KRW"),
    equity("SMSN.IL", "Samsung Electronics Co., Ltd.", "United Kingdom", "IOB", isin="US7960508882",
           currency="USD"),
]
FRESH = [bar("2026-09-22", 10.0), bar("2026-09-23", 11.0)]
YES = {"answer": "yes", "reason": "Fluence builds grid batteries"}


@pytest.fixture
def yahoo():
    y = FakeYahoo()
    for s in ("FLNC", "FLNC.DE", "BIDU", "SMSN.IL", "005930.KS"):
        y.add(s, "USD", "America/New_York", FRESH)
    return y


@pytest.fixture
async def ctx(tmp_path, yahoo):
    web = FakeWeb(FakeFinanceDatabase(UNIVERSE), yahoo, FakeGoogleNews(), FakeGdelt(), FakePages())
    with open_context(Config(db_path=tmp_path / "o.db"), None, FakeClock(NOW), web.transport) as c:
        await refresh_universe(c)
        for s in ("FLNC", "FLNC.DE", "BIDU", "SMSN.IL"):
            await update_listing(c, s)
        yield c


def news(ctx, *items, source="google_news", query=None) -> tuple[int, ...]:
    """(title, outlet, days ago) -> stored article ids."""
    articles = [Article(f"https://news.test/{source}/{title}/{outlet}/{ago}", title, outlet, NOW - timedelta(days=ago))
                for title, outlet, ago in items]
    return tuple(ArticleRepo(ctx.conn).save(articles, source, "2026-09-24T12:00:00Z", query))


def shortlisted(ctx, symbol="FLNC", evidence=(), name="Fluence Energy") -> ShortlistItem:
    p = UniverseRepo(ctx.conn).profile(symbol)
    return ShortlistItem(1, name, symbol, p.company_key, p.name, "Builds grid batteries", evidence)


def relevance(answer=YES):
    m = ScriptedModel()
    m.roles = {"relevance_check": answer}
    return m


async def validate(ctx, item, model=None):
    validation, counted = await Validator(ctx, model or relevance(), QUESTION, "scripted/script#q1").validate(item)
    return validation, counted


def outcome(validation):
    failed = validation.failed
    return (failed.name, failed.detail) if failed else "passed"


def two_outlets(ctx):
    return news(ctx, ("Fluence Energy wins grid storage deal", "Reuters", 3),
                ("Fluence Energy battery orders jump", "BBC", 10))


async def test_a_company_passing_every_check_is_valid_with_the_evidence_counted(ctx):
    ids = two_outlets(ctx)
    validation, counted = await validate(ctx, shortlisted(ctx, evidence=ids))
    assert outcome(validation) == "passed"
    assert [c.name for c in validation.checks] == ["resolved", "line", "prices", "never list", "removed", "receipt",
                                                   "evidence", "relevance"]
    assert set(counted) == set(ids)
    assert validation.checks[-1].detail == "yes: Fluence builds grid batteries"


async def test_a_symbol_no_longer_resolving_to_the_company_fails_resolved(ctx):
    item = replace(shortlisted(ctx, evidence=two_outlets(ctx)), company_key="another-company")
    validation, _ = await validate(ctx, item)
    assert outcome(validation)[0] == "resolved" and len(validation.checks) == 1


async def test_a_secondary_line_fails_line(ctx):
    validation, _ = await validate(ctx, shortlisted(ctx, "FLNC.DE", two_outlets(ctx)))
    assert outcome(validation) == ("line", "FLNC.DE is not its primary or most liquid live line")


async def test_stale_prices_fail_prices(ctx):
    ctx.clock.advance(timedelta(days=14).total_seconds())
    validation, _ = await validate(ctx, shortlisted(ctx, evidence=two_outlets(ctx)))
    assert outcome(validation) == ("prices", "no recent prices (last 2026-09-23)")


async def test_a_company_on_the_never_list_fails_never_list(ctx):
    ctx.config = replace(ctx.config, discovery=replace(ctx.config.discovery, never=("Tesla", "Fluence")))
    validation, _ = await validate(ctx, shortlisted(ctx, evidence=two_outlets(ctx)))
    assert outcome(validation) == ("never list", "on the never list")


async def test_a_company_the_user_removed_fails_removed(ctx):
    watch(ctx, "FLNC.DE", DISCOVERED_LIST)
    assert remove_discovered(ctx, "FLNC.DE")
    validation, _ = await validate(ctx, shortlisted(ctx, evidence=two_outlets(ctx)))
    assert outcome(validation) == ("removed", "you removed it before")


async def test_a_depositary_receipt_fails_receipt(ctx):
    ids = news(ctx, ("Baidu grid battery unit expands", "Reuters", 2), ("Baidu storage push", "BBC", 2))
    validation, _ = await validate(ctx, shortlisted(ctx, "BIDU", ids, "Baidu"))
    assert outcome(validation) == ("receipt", "Baidu, Inc. ADR is a depositary receipt of a company listed elsewhere")


async def test_another_line_of_a_watched_company_fails_receipt(ctx):
    watch(ctx, "005930.KS")
    ids = news(ctx, ("Samsung Electronics grid battery cells", "Reuters", 2), ("Samsung Electronics storage", "BBC", 2))
    validation, _ = await validate(ctx, shortlisted(ctx, "SMSN.IL", ids, "Samsung Electronics"))
    name, detail = outcome(validation)
    assert name == "receipt" and "Samsung Electronics Co., Ltd. (005930.KS), which you watch" in detail


async def test_one_outlet_fails_evidence_however_many_items(ctx):
    ids = news(ctx, ("Fluence Energy wins grid storage deal", "Reuters", 3),
               ("Fluence Energy battery orders jump", "Reuters", 4))
    m = relevance()
    validation, counted = await validate(ctx, shortlisted(ctx, evidence=ids), m)
    assert outcome(validation) == ("evidence", "only 1 outlet (reuters) named it in the last 90 days; 2 are needed")
    assert counted == () and m.calls == [], "no model call once a check fails"


async def test_old_items_and_items_not_naming_the_company_do_not_count(ctx):
    ids = news(ctx, ("Fluence Energy wins grid storage deal", "Reuters", 3),
               ("Fluence Energy battery orders jump", "BBC", 91),
               ("Grid batteries boom in Europe", "FT", 1))
    validation, _ = await validate(ctx, shortlisted(ctx, evidence=ids))
    assert outcome(validation)[0] == "evidence"


async def test_the_company_s_own_recent_filings_count_as_evidence(ctx):
    ids = news(ctx, ("Fluence Energy wins grid storage deal", "Reuters", 3))
    [filing] = news(ctx, ("10-Q quarterly report", "SEC", 20), source="sec_edgar", query="FLNC")
    news(ctx, ("8-K current report", "SEC", 120), source="sec_edgar", query="FLNC")
    validation, counted = await validate(ctx, shortlisted(ctx, evidence=ids))
    assert outcome(validation) == "passed"
    assert set(counted) == {*ids, filing}


async def test_a_no_from_the_relevance_check_fails_it_with_the_reason(ctx):
    validation, _ = await validate(ctx, shortlisted(ctx, evidence=two_outlets(ctx)),
                                   relevance({"answer": "no", "reason": "The items are about wind farms"}))
    assert outcome(validation) == ("relevance", "no: The items are about wind farms")


@pytest.mark.parametrize("reply", [{"answer": "maybe", "reason": "unsure"}, {"answer": "yes", "reason": ""},
                                   "yes", None])
async def test_a_garbled_relevance_answer_fails_it(ctx, reply):
    validation, _ = await validate(ctx, shortlisted(ctx, evidence=two_outlets(ctx)), relevance(reply))
    assert outcome(validation) == ("relevance", "the model's answer could not be read")


async def test_a_model_that_cannot_answer_fails_relevance_and_raises_nothing(ctx):
    validation, _ = await validate(ctx, shortlisted(ctx, evidence=two_outlets(ctx)),
                                   relevance(ModelError("ollama is not running")))
    assert outcome(validation) == ("relevance", "the check could not run (ollama is not running)")


async def test_the_relevance_call_reads_only_the_question_the_company_and_the_cited_items(ctx):
    ids = two_outlets(ctx)
    news(ctx, ("Fluence Energy unrelated story", "FT", 1))
    m = relevance()
    await validate(ctx, shortlisted(ctx, evidence=ids), m)
    [(messages, schema)] = m.calls
    user = messages[1]["content"]
    assert user.startswith(f"Question: {QUESTION}\nCompany: Fluence Energy, Inc. (FLNC)\nNews items:\n[1] ")
    assert "wins grid storage deal" in user and "orders jump" in user and "unrelated" not in user
    assert schema["properties"]["answer"]["enum"] == ["yes", "no"]

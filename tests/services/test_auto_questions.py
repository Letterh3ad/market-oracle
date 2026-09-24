"""Auto questions: each answer adds the shortlisted companies that pass validation to the
discovered list, within the caps, and keeps why the rest stayed suggestions."""

from dataclasses import replace
from datetime import datetime, timezone

import pytest

from oracle.services import tick_service
from oracle.services.discovery_chat_service import (
    answer, answer_auto_questions, new_question, question, questions, set_auto,
)
from oracle.services.discovery_service import discovered
from oracle.services.errors import UnknownQuestion
from oracle.services.paper_service import traded_by
from oracle.services.tick_service import run_midday
from oracle.services.watch_service import watch
from oracle.store.discoveries import DISCOVERED_LIST
from tests.fakes.google_news import FakeGoogleNews
from tests.fakes.web_search import FakeSearcher
from tests.services.test_discovery_chat import GRID, ctx, gdelt, model, names, pages, web, yahoo  # noqa: F401

# Siemens Energy is named by Reuters, BBC and Handelsblatt; Fluence by BBC and Reuters; RWE by Reuters alone.
ITEMS = [
    ("Siemens Energy wins grid battery order in Germany", "Reuters"),
    ("Fluence Energy and Siemens Energy team up on grid storage", "BBC"),
    ("RWE switches on its biggest grid battery", "Reuters"),
    ("Tesla Megapacks head to European grid", "Wire"),
    ("Siemens Energy battery plant opens", "Handelsblatt"),
    ("Fluence Energy lands grid storage deal", "Reuters"),
]
NAMED = names(("Siemens Energy", "Wins grid battery orders", [1, 2, 5]), ("Fluence Energy", "Grid storage", [2, 6]),
              ("RWE", "Runs grid batteries", [3]), ("Tesla", "Sells Megapacks", [4]))


@pytest.fixture
def news():
    n = FakeGoogleNews()
    n.results["grid battery Europe"] = [(f"https://news.test/{i}", t, outlet,
                                         datetime(2026, 9, 23, 20 - i, tzinfo=timezone.utc))
                                        for i, (t, outlet) in enumerate(ITEMS)]
    return n


def judged(no=(), answers=1):
    """A model naming NAMED whose relevance check says yes but for companies in `no`."""
    m = model(*[NAMED] * answers)

    def relevance(messages):
        company = messages[1]["content"].split("\nCompany: ", 1)[1].split("\n", 1)[0]
        return ({"answer": "no", "reason": "Not about grid batteries"} if any(n in company for n in no)
                else {"answer": "yes", "reason": "Builds grid batteries"})

    m.roles = {"relevance_check": relevance}
    return m


def relevance_calls(m):
    return [c for c in m.calls if c[0][0]["content"].startswith("Role: relevance_check.")]


def settings(ctx, **values):
    ctx.config = replace(ctx.config, discovery=replace(ctx.config.discovery, **values))


def by_symbol(view):
    return {line.item.symbol: line.item.validation for line in view.shortlist}


async def auto_question(ctx):
    q = new_question(ctx, GRID)
    assert set_auto(ctx, q.id, True).auto
    return q


async def test_an_auto_question_adds_what_passes_and_names_the_failing_check_on_the_rest(ctx):
    q = await auto_question(ctx)
    view = await answer(ctx, q.id, judged(no=("Fluence",)), FakeSearcher())
    v = by_symbol(view)
    assert v["ENR.DE"].added and v["ENR.DE"].failed is None and len(v["ENR.DE"].checks) == 8
    assert (v["FLNC"].added, v["FLNC"].failed.name, v["FLNC"].failed.detail) == (
        False, "relevance", "no: Not about grid batteries")
    assert (v["RWE.DE"].added, v["RWE.DE"].failed.name) == (False, "evidence")
    assert view.skipped[0].validation is None, "Tesla was never a candidate"
    assert "auto: added ENR.DE; 2 stayed suggestions (why is on each)" in view.answer.notes
    [d] = discovered(ctx)
    assert (d.symbol, d.source, d.relation, d.question_id) == ("ENR.DE", "question", "auto", q.id)
    assert d.why == f'added by your auto question "{GRID}" after passing its checks'
    assert d.validation.added and [c.name for c in d.validation.checks][-2:] == ["evidence", "relevance"]
    assert {e.outlet for e in d.evidence} == {"Reuters", "BBC", "Handelsblatt"}
    saved = question(ctx, q.id).answers[0]
    assert by_symbol(saved) == v, "the validation is saved with the answer"


async def test_a_question_not_on_auto_adds_nothing_and_asks_no_relevance(ctx):
    q = new_question(ctx, GRID)
    m = judged()
    view = await answer(ctx, q.id, m, FakeSearcher())
    assert discovered(ctx) == [] and relevance_calls(m) == []
    assert all(v is None for v in by_symbol(view).values())


async def test_the_per_question_cap_stops_validation_once_reached(ctx):
    settings(ctx, per_question=1)
    q = await auto_question(ctx)
    m = judged()
    view = await answer(ctx, q.id, m, FakeSearcher())
    v = by_symbol(view)
    assert v["ENR.DE"].added and not v["FLNC"].checks
    assert v["FLNC"].note == "not validated: this question already has 1 of its 1 on the discovered list " \
                             "(Settings, per_question)"
    assert len(relevance_calls(m)) == 1 and [d.symbol for d in discovered(ctx)] == ["ENR.DE"]


async def test_a_full_discovered_list_stops_validation(ctx):
    settings(ctx, max_total=1)
    watch(ctx, "NIBE-B.ST", DISCOVERED_LIST)
    q = await auto_question(ctx)
    m = judged()
    view = await answer(ctx, q.id, m, FakeSearcher())
    assert by_symbol(view)["ENR.DE"].note.startswith("not validated: the discovered list is full (1 of 1)")
    assert relevance_calls(m) == [] and [d.symbol for d in discovered(ctx)] == ["NIBE-B.ST"]


async def test_watched_and_discovered_companies_are_left_alone(ctx):
    watch(ctx, "ENR.DE")
    q = await auto_question(ctx)
    view = await answer(ctx, q.id, judged(), FakeSearcher())
    v = by_symbol(view)
    assert v["ENR.DE"] is None and v["FLNC"].added


async def test_the_ai_account_trades_an_auto_added_company(ctx):
    q = await auto_question(ctx)
    await answer(ctx, q.id, judged(), FakeSearcher())
    assert {"ENR.DE", "FLNC"} <= set(traded_by(ctx, "ai"))


async def test_auto_can_be_switched_off_and_an_unknown_question_is_refused(ctx):
    q = await auto_question(ctx)
    assert [line.question.auto for line in questions(ctx)] == [True]
    assert not set_auto(ctx, q.id, False).auto
    with pytest.raises(UnknownQuestion):
        set_auto(ctx, 99, True)


async def test_auto_questions_are_answered_again_longest_ago_first_up_to_a_limit(ctx):
    first, second = await auto_question(ctx), await auto_question(ctx)
    new_question(ctx, "not on auto")
    await answer(ctx, first.id, judged(), FakeSearcher())
    ctx.clock.advance(60)
    run = await answer_auto_questions(ctx, 1, judged(), FakeSearcher())
    assert [v.answer.question_id for v in run.answered] == [second.id]
    run = await answer_auto_questions(ctx, 5, judged(answers=2), FakeSearcher())
    assert [v.answer.question_id for v in run.answered] == [first.id, second.id]


async def test_no_auto_question_or_a_full_list_asks_nothing(ctx):
    assert (await answer_auto_questions(ctx, 3, judged())).note == "no question is on auto"
    await auto_question(ctx)
    settings(ctx, max_total=1)
    watch(ctx, "NIBE-B.ST", DISCOVERED_LIST)
    m = judged()
    run = await answer_auto_questions(ctx, 3, m)
    assert run.answered == () and run.note.startswith("the discovered list is full") and m.calls == []


async def test_the_midday_pass_answers_auto_questions_between_discovery_and_research(ctx, monkeypatch):
    q = await auto_question(ctx)
    m = judged()

    async def answering(c, limit):
        return await answer_auto_questions(c, limit, m, FakeSearcher())

    monkeypatch.setattr(tick_service, "answer_auto_questions", answering)
    monkeypatch.setattr(tick_service, "_discover", _nothing)
    monkeypatch.setattr(tick_service, "_research", _nothing)
    run = await run_midday(ctx)
    stage = next(s for s in run.stages if s.name == "questions")
    assert stage.summary == "answered 1 auto question, added ENR.DE, FLNC"
    assert question(ctx, q.id).answers[0].answer.status == "done"


async def _nothing(*_, **__):
    return "skipped in this test", None

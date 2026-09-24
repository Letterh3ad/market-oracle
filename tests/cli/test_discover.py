from datetime import datetime, timezone
from functools import partial

import pytest

from oracle.cli import main as cli_main
from oracle.services import candidate_screen, discovery_chat_service, discovery_service
from oracle.services.context import open_context
from tests.fakes.financedatabase import FakeFinanceDatabase, equity
from tests.fakes.google_news import FakeGoogleNews
from tests.fakes.llm import ScriptedModel
from tests.fakes.web import FakeWeb
from tests.fakes.web_search import FakeSearcher
from tests.fakes.yahoo import FakeYahoo, bar

CARS = dict(currency="USD", sector="Consumer Discretionary", industry_group="Automobiles & Components",
            market_cap="Large Cap")


@pytest.fixture
def news():
    return FakeGoogleNews()


@pytest.fixture
def chat():
    """The model answering questions: its plan searches "rivals", then the replies naming companies."""
    m = ScriptedModel()
    m.suggestions = {"queries": ["rivals"], "local": [], "market_caps": [], "exclude": []}
    return m


@pytest.fixture
def run(tmp_path, capsys, monkeypatch, news, chat):
    cfg = tmp_path / "oracle.toml"
    cfg.write_text("".join(f"[sources.{s}]\nrate_per_second = 1000\n"
                           for s in ("financedatabase", "yahoo", "google_news", "gdelt")), encoding="utf-8")
    yahoo = FakeYahoo()
    names = {"F": "Ford Motor Company", "GM": "General Motors Company", "TSLA": "Tesla, Inc.",
             "STLA": "Stellantis N.V.", "RIVN": "Rivian Automotive, Inc.", "HMC": "Honda Motor Co., Ltd."}
    for s in names:
        yahoo.add(s, "USD", "America/New_York", [bar("2026-09-21", 10.0), bar("2026-09-22", 11.0)])
    web = FakeWeb(FakeFinanceDatabase([equity(s, n, **CARS) for s, n in names.items()]), yahoo, news)
    monkeypatch.setattr(cli_main, "open_context", partial(open_context, transport=web.transport))
    monkeypatch.setattr(discovery_service, "model_for", lambda c, p: ScriptedModel())
    monkeypatch.setattr(candidate_screen, "is_stale", lambda last, today: False)
    monkeypatch.setattr(discovery_chat_service, "model_for", lambda c, p: chat)
    monkeypatch.setattr(discovery_chat_service, "DdgsSearcher", FakeSearcher)

    def _run(*argv):
        code = cli_main.main(["--config", str(cfg), *argv])
        out, err = capsys.readouterr()
        return code, out, err

    _run("universe", "refresh")
    return _run


def test_discover_with_nothing_watched_says_where_it_starts(run):
    code, out, _ = run("discover")
    assert code == 0 and "nothing watched yet" in out


def test_discover_adds_a_related_company_and_says_why(run):
    run("watch", "add", "F")
    code, out, _ = run("discover")
    assert code == 0
    lines = out.splitlines()
    assert lines[0] == "Discovery from F: added 1"
    assert "added GM" in out and "same industry as F" in out
    assert "could not use news for F from GDELT" in out, "a failed source is said, not fatal"
    assert "GM" in run("watch", "show", "discovered")[1]


def rivals(news, *titles):
    news.results["rivals"] = [(f"https://news.test/{i}", t, "Wire", datetime(2026, 9, 22, 20 - i, tzinfo=timezone.utc))
                              for i, t in enumerate(titles)]


def naming(*entries):
    return {"companies": [{"name": n, "why": w, "headlines": list(h)} for n, w, h in entries]}


def test_ask_prints_a_shortlist_with_why_evidence_and_what_was_left_off(run, news, chat):
    rivals(news, "Rivian and Ford race on electric trucks", "Tesla cuts truck prices")
    chat.replies = [naming(("Rivian", "Electric truck maker", [1]), ("Ford", "Trucks", [1]),
                           ("Tesla", "Trucks", [2]))]
    run("watch", "add", "F")
    code, out, _ = run("discover", "ask", "which carmakers gain from electric trucks?")
    assert code == 0
    assert "Answer 1 to question 1" in out and "Searched Google News: rivals" in out
    assert " 1. RIVN  Rivian Automotive, Inc." in out and "Electric truck maker" in out
    assert " 2. F  Ford Motor Company [watched]" in out
    assert "https://news.test/0" in out and "left off Tesla: on the never list" in out
    assert "oracle discover add 1 RANK" in out


def test_a_follow_up_a_rerun_listing_showing_and_adding(run, news, chat):
    rivals(news, "Rivian and Honda race on electric trucks")
    chat.replies = [naming(("Rivian", "Trucks", [1])), naming(("Honda", "Hybrids", [1])),
                    naming(("Rivian", "Trucks", [1]), ("Honda", "Hybrids", [1]))]
    run("discover", "ask", "which carmakers gain from electric trucks?")
    code, out, _ = run("discover", "ask", "only Japanese ones", "--refines", "1")
    assert code == 0 and "1. HMC  Honda Motor Co., Ltd. [new]" in out and "gone since answer 1: RIVN" in out
    code, out, _ = run("discover", "rerun", "1")
    assert "HMC  Honda Motor Co., Ltd. [new]" in out and "RIVN  Rivian Automotive, Inc.\n" in out
    code, out, _ = run("discover", "questions")
    assert "   2  only Japanese ones (follow-up to 1)" in out and "2 answer(s), last done" in out
    code, out, _ = run("discover", "show", "1")
    assert "Question 1: which carmakers gain" in out and "follow-up 2: only Japanese ones" in out
    code, out, _ = run("discover", "add", "3", "2")
    assert code == 0 and "added HMC" in out
    assert "HMC" in run("watch", "show", "discovered")[1]


def test_a_blank_question_is_refused(run):
    code, _, err = run("discover", "ask", " ")
    assert code == 1 and "ask a question" in err


def test_auto_switches_a_question_and_an_auto_answer_says_what_validation_made_of_each(run, news, chat):
    rivals(news, "Rivian and Honda race on electric trucks")
    chat.replies = [naming(("Rivian", "Trucks", [1])), naming(("Rivian", "Trucks", [1]))]
    chat.roles = {"relevance_check": {"reason": "Makes trucks", "items": [1], "answer": "yes"}}
    run("discover", "ask", "which carmakers gain from electric trucks?")
    code, out, _ = run("discover", "auto", "1", "on")
    assert code == 0 and "question 1 is on auto" in out
    assert "which carmakers gain from electric trucks? [auto]" in run("discover", "questions")[1]
    code, out, _ = run("discover", "rerun", "1")
    assert code == 0 and "auto: stays a suggestion: failed" in out, "one outlet is not enough"
    code, out, _ = run("discover", "auto", "1", "off")
    assert "question 1 is off auto" in out
    code, _, err = run("discover", "auto", "9", "on")
    assert code == 1 and "no question 9" in err

from functools import partial

import pytest

from oracle.cli import main as cli_main
from oracle.services.context import open_context
from datetime import datetime

from tests.fakes.financedatabase import FakeFinanceDatabase, equity
from tests.fakes.google_news import FakeGoogleNews
from tests.fakes.llm import step
from tests.fakes.ollama import FakeOllama
from tests.fakes.web import FakeWeb
from tests.fakes.yahoo import FakeYahoo, bar


@pytest.fixture
def ollama():
    return FakeOllama()


@pytest.fixture
def news():
    return FakeGoogleNews()


@pytest.fixture
def run(tmp_path, capsys, monkeypatch, ollama, news):
    cfg = tmp_path / "oracle.toml"
    cfg.write_text(
        "[sources.financedatabase]\nrate_per_second = 1000\n[sources.yahoo]\nrate_per_second = 1000\n",
        encoding="utf-8",
    )
    yahoo = FakeYahoo()
    yahoo.add("AAPL", "USD", "America/New_York", [bar("2026-09-18", 100.0), bar("2026-09-21", 105.0)])
    web = FakeWeb(FakeFinanceDatabase([equity("AAPL", "Apple Inc.", currency="USD")]), yahoo, ollama, news)
    monkeypatch.setattr(cli_main, "open_context", partial(open_context, transport=web.transport))

    def _run(*argv):
        code = cli_main.main(["--config", str(cfg), *argv])
        out, err = capsys.readouterr()
        return code, out, err

    for argv in (["universe", "refresh"], ["prices", "update", "AAPL"]):
        _run(*argv)
    return _run


def test_research_prints_each_step_and_the_findings(run, ollama):
    ollama.contents = [step("market_data", "AAPL", thought="check the price"),
                       step("final", "Up 5% on the day; nothing else known.", thought="done")]
    code, out, _ = run("research", "AAPL")
    assert code == 0
    assert "1. check the price" in out and "market_data(AAPL)" in out and "Last close 105.00 USD" in out
    assert "Findings (ollama qwen3:4b-instruct-2507-q8_0):" in out and "Up 5% on the day" in out


def test_a_capped_run_says_so(run, ollama, tmp_path):
    cfg = tmp_path / "oracle.toml"
    cfg.write_text(cfg.read_text(encoding="utf-8") + "[llm]\nmax_steps = 1\n", encoding="utf-8")
    ollama.contents = [step("market_data"), step("final", "Partial.")]
    code, out, _ = run("research", "AAPL")
    assert code == 0 and "stopped at the 1-step limit" in out


def test_ollama_not_running_is_a_plain_error(run, ollama):
    ollama.down = True
    code, _, err = run("research", "AAPL")
    assert code == 1 and "Ollama is not reachable" in err and "ollama serve" in err and "Traceback" not in err


def test_the_evidence_cited_is_listed_with_links(run, ollama, news):
    news.results["Apple"] = [
        ("https://reut.rs/1", "Apple beats estimates", "Reuters", datetime.fromisoformat("2026-09-20T12:00:00+00:00")),
        ("https://bbc.in/2", "Unrelated", "BBC", None),
    ]
    ollama.contents = [step("news_search", "Apple"), step("final", "Beat estimates [a1].")]
    code, out, _ = run("research", "AAPL")
    assert "Evidence cited:" in out and "[a1] 2026-09-20 Reuters: Apple beats estimates" in out
    assert "https://reut.rs/1" in out and "https://bbc.in/2" not in out.split("Evidence cited:")[1]


def test_each_article_shown_gets_its_own_line(run, ollama, news):
    news.results["Apple"] = [("https://x.com/1", "First", "X", None), ("https://x.com/2", "Second", "X", None)]
    ollama.contents = [step("news_search", "Apple"), step("final", "Done.")]
    _, out, _ = run("research", "AAPL")
    assert "   > [a1] undated X: First\n   > [a2] undated X: Second\n" in out


def test_a_replay_as_of_a_past_day_says_so(run, ollama):
    ollama.contents = [step("final", "Done.")]
    code, out, _ = run("research", "AAPL", "--as-of", "2026-09-18")
    assert code == 0 and "AAPL, as of 2026-09-18, researched" in out


def test_an_as_of_date_must_be_a_real_past_date(run, ollama):
    code, _, err = run("research", "AAPL", "--as-of", "2999-01-01")
    assert code == 1 and "in the future" in err
    with pytest.raises(SystemExit):
        run("research", "AAPL", "--as-of", "18/09/2026")


def test_the_planned_searches_are_shown_first(run, ollama):
    ollama.suggestions = ["Apple iPhone demand"]
    ollama.contents = [step("final", "Done.")]
    _, out, _ = run("research", "AAPL")
    assert "Searches: Apple shares; Apple earnings outlook; Apple iPhone demand" in out


def test_text_the_console_cannot_encode_is_replaced_not_a_crash(run, ollama, monkeypatch):
    import io
    import sys

    ollama.suggestions = ["トヨタ 株価"]
    ollama.contents = [step("final", "Done.")]
    raw = io.BytesIO()
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(raw, encoding="cp1252"))
    code, _, _ = run("research", "AAPL")
    sys.stdout.flush()
    assert code == 0 and b"Searches: Apple shares; Apple earnings outlook; ??? ??" in raw.getvalue()


def scripted_report(ollama, news, bull="Earnings momentum"):
    news.results["Apple"] = [("https://reut.rs/1", "Apple beats estimates", "Reuters",
                              datetime.fromisoformat("2026-09-20T12:00:00+00:00"))]
    ollama.contents = [step("news_search", "Apple"), step("final", "Beat [a1].")]
    ollama.roles = {
        "news_analyst": {"findings": [{"text": "Beat estimates", "cites": ["a1"]}]},
        "bull": {"case": bull, "cites": ["a1"]},
        "bear": {"case": "Priced for perfection", "cites": ["a2"]},
        "risk": {"risks": [{"text": "China demand", "cites": ["a1"]}], "invalidated_if": "Next quarter misses"},
    }


def test_the_report_is_printed_with_its_badge_and_evidence(run, ollama, news):
    scripted_report(ollama, news)
    code, out, _ = run("research", "AAPL")
    report = out.split("Report (unvalidated")[1]
    assert code == 0 and "Bull: Earnings momentum [a1]" in report and "Bear: Priced for perfection [a2]" in report
    assert "- news_analyst: Beat estimates [a1]" in report and "- China demand [a1]" in report
    assert "Invalidated if: Next quarter misses" in report
    assert "[a2]" in out.split("Evidence cited:")[1] and "stored data" in out.split("Evidence cited:")[1]


def test_history_lists_past_reports_without_running(run, ollama, news):
    for bull in ("First view", "Second view"):
        scripted_report(ollama, news, bull)
        run("research", "AAPL")
    code, out, _ = run("research", "AAPL", "--history")
    assert code == 0 and out.index("Second view") < out.index("First view")
    assert ollama.contents == []  # nothing new was asked of the model


def test_what_the_checks_rejected_is_printed_with_the_report(run, ollama, news):
    scripted_report(ollama, news)
    ollama.roles["risk"] = "garbled"
    _, out, _ = run("research", "AAPL")
    assert "Rejected: 1 of 6 roles gave an unusable answer" in out.split("Report (unvalidated")[1]


def test_a_run_with_no_usable_answers_says_so_and_exits_nonzero(run, ollama, news):
    scripted_report(ollama, news)
    ollama.roles = dict.fromkeys(("fundamental_analyst", "technical_analyst", "news_analyst", "bull", "bear",
                                  "risk"), "garbled")
    code, out, _ = run("research", "AAPL")
    assert code == 1 and "Failed: the model's answers were unusable" in out and "Report (" not in out


VERDICT = {"verdict": "buy", "confidence": 0.7, "reason": "Beat estimates", "cites": ["a1"],
           "invalidated_if": "Guidance is cut"}


def test_the_verdict_comes_from_the_default_ollama_model_and_is_printed_badged(run, ollama, news):
    scripted_report(ollama, news)
    ollama.roles["verdict"] = VERDICT
    code, out, _ = run("research", "AAPL")
    [asked] = [b for b in ollama.bodies if b["messages"][0]["content"].startswith("Role: verdict.")]
    assert asked["model"] == "qwen3:4b-instruct-2507-q8_0" and asked["format"]["properties"]["verdict"]["enum"] == [
        "buy", "hold", "sell"]
    report = out.split("Report (unvalidated")[1]
    assert code == 0 and "Verdict (unvalidated): BUY, confidence 70%: Beat estimates [a1]" in report
    assert "Verdict wrong if: Guidance is cut" in report and "did not read" not in report
    _, out, _ = run("research", "AAPL", "--history")
    assert "verdict: BUY, confidence 70%" in out


def test_a_malformed_verdict_is_printed_as_none_with_the_reason(run, ollama, news):
    scripted_report(ollama, news)
    ollama.roles["verdict"] = "I think you should buy"
    code, out, _ = run("research", "AAPL")
    assert code == 0
    assert "Verdict (unvalidated): none (the verdict was rejected: not in the shape asked for)" in out


def test_what_the_verdict_did_not_read_is_printed(capsys):
    from datetime import date

    from oracle.cli.research import print_report
    from oracle.services.research_service import Report

    print_report(Report(date(2026, 9, 21), (), None, None, (), None, unread=("7 uncited items",)))
    out = capsys.readouterr().out
    assert "Verdict (unvalidated): none (written before reports had verdicts)" in out
    assert "The verdict did not read: 7 uncited items" in out

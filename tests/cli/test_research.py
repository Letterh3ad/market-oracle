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

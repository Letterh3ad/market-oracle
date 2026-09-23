from functools import partial

import pytest

from oracle.cli import main as cli_main
from oracle.services.context import open_context
from tests.fakes.financedatabase import FakeFinanceDatabase, equity
from tests.fakes.llm import step
from tests.fakes.ollama import FakeOllama
from tests.fakes.web import FakeWeb
from tests.fakes.yahoo import FakeYahoo, bar


@pytest.fixture
def ollama():
    return FakeOllama()


@pytest.fixture
def run(tmp_path, capsys, monkeypatch, ollama):
    cfg = tmp_path / "oracle.toml"
    cfg.write_text(
        "[sources.financedatabase]\nrate_per_second = 1000\n[sources.yahoo]\nrate_per_second = 1000\n",
        encoding="utf-8",
    )
    yahoo = FakeYahoo()
    yahoo.add("AAPL", "USD", "America/New_York", [bar("2026-09-18", 100.0), bar("2026-09-21", 105.0)])
    web = FakeWeb(FakeFinanceDatabase([equity("AAPL", "Apple Inc.", currency="USD")]), yahoo, ollama)
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

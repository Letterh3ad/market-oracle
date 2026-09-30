from functools import partial

import pytest

from oracle.cli import main as cli_main
from oracle.services.context import load_settings, open_context
from oracle.services.watch_service import watch
from tests.fakes.financedatabase import UNIVERSE, FakeFinanceDatabase
from tests.fakes.google_news import FakeGoogleNews
from tests.fakes.web import FakeWeb
from tests.fakes.yahoo import FakeYahoo


@pytest.fixture
def cli(tmp_path, capsys, monkeypatch):
    cfg = tmp_path / "oracle.toml"
    cfg.write_text("[sources.financedatabase]\nrate_per_second = 1000\n[sources.google_news]\nrate_per_second = 1000\n"
                   "[sources.yahoo]\nrate_per_second = 1000\n", encoding="utf-8")
    news = FakeGoogleNews()
    web = FakeWeb(FakeFinanceDatabase(UNIVERSE), FakeYahoo(), news)
    monkeypatch.setattr(cli_main, "open_context", partial(open_context, transport=web.transport))

    def run(*argv):
        code = cli_main.main(["--config", str(cfg), *argv])
        out, err = capsys.readouterr()
        return code, out, err

    run("universe", "refresh")
    with open_context(*load_settings(cfg)) as ctx:
        watch(ctx, "KO")
    run.news = news
    return run


def test_backfill_then_coverage_prints_what_was_asked_and_what_is_stored(cli):
    code, out, _ = cli("history", "backfill")
    assert code == 1, "finished with a failure: no SEC contact"
    assert "requests" in out and "windows" in out
    assert 'filings skipped: SEC needs [research] contact' in out
    assert cli.news.queries and all("after:" in q and "before:" in q for q in cli.news.queries)
    code, out, _ = cli("history", "coverage")
    lines = out.splitlines()
    assert code == 0 and lines[0].startswith("History coverage from 2022-09-01")
    assert "Caveat: today's list and maps taken back in time" in out
    assert any(line.startswith("KO ") and "main list" in line for line in lines)
    assert "0/0*" in out, "no news stored: every quarter thin"


def test_an_unknown_list_is_refused_plainly(cli):
    code, _, err = cli("history", "coverage", "--list", "nope")
    assert code != 0 and "nope" in err


def test_a_search_name_is_shown_set_and_cleared(cli):
    code, out, _ = cli("history", "name", "KO")
    assert code == 0 and out.startswith("KO: ") and "(default)" in out
    code, out, _ = cli("history", "name", "KO", "--query", "Coca-Cola", "--alias", "Coke")
    assert code == 0 and "KO: saved" in out and '"Coca-Cola" (user); aliases Coke' in out
    code, out, _ = cli("history", "name", "KO", "--clear")
    assert code == 0 and "back to the default name" in out
    code, _, err = cli("history", "name", "KO", "--clear")
    assert code != 0 and "no search name of yours" in err

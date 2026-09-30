from datetime import datetime, timezone
from functools import partial

import pytest

from oracle.cli import main as cli_main
from oracle.research.news import Article
from oracle.services.context import load_settings, open_context
from oracle.services.links_service import LinkFound, record_links
from oracle.store.articles import ArticleRepo
from tests.fakes.financedatabase import UNIVERSE, FakeFinanceDatabase
from tests.fakes.web import FakeWeb
from tests.fakes.yahoo import FakeYahoo


@pytest.fixture
def cli(tmp_path, capsys, monkeypatch):
    cfg = tmp_path / "oracle.toml"
    cfg.write_text("[sources.financedatabase]\nrate_per_second = 1000\n", encoding="utf-8")
    web = FakeWeb(FakeFinanceDatabase(UNIVERSE), FakeYahoo())
    monkeypatch.setattr(cli_main, "open_context", partial(open_context, transport=web.transport))

    def run(*argv):
        code = cli_main.main(["--config", str(cfg), *argv])
        out, err = capsys.readouterr()
        return code, out, err

    run("universe", "refresh")
    with open_context(*load_settings(cfg)) as ctx:
        [a] = ArticleRepo(ctx.conn).save([Article("https://news.test/1", "Toyota and Apple talk", "Wire",
                                                  datetime(2026, 9, 22, tzinfo=timezone.utc))], "google_news",
                                         "2026-09-23T00:00:00Z")
        record_links(ctx, [LinkFound("7203.T", "AAPL", "partner", (a,))], "discovery")
    return run


def test_links_show_lists_each_link_with_its_headlines_and_delete_removes_it_for_good(cli):
    code, out, _ = cli("links", "show", "AAPL")
    lines = out.splitlines()
    assert code == 0 and lines[0].startswith("#1  7203.T  Toyota Motor Corporation: a partner (found by discovery")
    assert lines[1].strip() == "2026-09-22 Wire: Toyota and Apple talk"
    code, out, _ = cli("links", "delete", "TM", "1")
    assert code == 0 and "deleted the link to AAPL" in out
    assert cli("links", "show", "AAPL")[1].strip() == "no links for AAPL"


def test_deleting_a_link_the_company_does_not_have_fails_plainly(cli):
    code, _, err = cli("links", "delete", "KO", "1")
    assert code != 0 and "no link 1 for KO" in err


def test_links_add_keeps_the_users_own_link(cli):
    code, out, _ = cli("links", "add", "7203.T", "KO", "customer")
    assert code == 0 and "KO (The Coca-Cola Company) is now a customer of 7203.T" in out
    assert "KO" in cli("links", "show", "7203.T")[1]
    code, _, err = cli("links", "add", "7203.T", "KO", "friend")
    assert code != 0 and "one of supplier, customer, competitor, partner" in err


def test_industry_prints_each_related_company_how_since_when_and_now(cli):
    code, out, _ = cli("industry", "TM")
    assert code == 0 and out.startswith("Industry map of 7203.T (Toyota Motor Corporation)")
    row = next(line for line in out.splitlines() if line.startswith("AAPL"))
    assert "a partner (discovery, first known 20" in row
    assert "5d n/a  20d n/a  last earnings n/a  next earnings n/a" in out
    assert "2026-09-22 Wire: Toyota and Apple talk" in out

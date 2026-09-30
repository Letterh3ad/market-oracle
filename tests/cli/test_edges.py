from datetime import datetime, timezone
from functools import partial

import pytest

from oracle.cli import main as cli_main
from oracle.research.news import Article
from oracle.services.context import load_settings, open_context
from oracle.store.articles import ArticleRepo
from oracle.store.research import ResearchRepo
from tests.fakes.financedatabase import FakeFinanceDatabase, equity
from tests.fakes.laya import FakeLaya
from tests.fakes.web import FakeWeb
from tests.fakes.yahoo import FakeYahoo

ROWS = [equity("7203.T", "Toyota Motor Corporation", "Japan", "JPX", market_cap="Mega Cap"),
        equity("6902.T", "DENSO Corporation", "Japan", "JPX", market_cap="Large Cap"),
        equity("BWA", "BorgWarner Inc.", market_cap="Mid Cap")]


@pytest.fixture
def cli(tmp_path, capsys, monkeypatch):
    cfg = tmp_path / "oracle.toml"
    cfg.write_text("[sources.financedatabase]\nrate_per_second = 1000\n", encoding="utf-8")
    web = FakeWeb(FakeFinanceDatabase(ROWS), FakeYahoo())
    monkeypatch.setattr(cli_main, "open_context", partial(open_context, transport=web.transport))
    monkeypatch.setattr("oracle.services.edges_service.load_scorer", lambda ctx: FakeLaya())

    def run(*argv):
        code = cli_main.main(["--config", str(cfg), *argv])
        out, err = capsys.readouterr()
        return code, out, err

    run("universe", "refresh")
    with open_context(*load_settings(cfg)) as ctx:
        ids = ArticleRepo(ctx.conn).save(
            [Article(f"https://news.test/{n}", t, "Wire", datetime(2026, 9, n, tzinfo=timezone.utc))
             for n, t in ((3, "Toyota picks Denso as battery supplier"), (4, "BorgWarner orders parts from Toyota"))],
            "google_news", "2026-09-23T00:00:00Z")
        repo = ResearchRepo(ctx.conn)
        repo.add_evidence(repo.start("7203.T", "ollama", "m", "2026-09-23T00:00:00Z"), ids)
    return run


def test_find_show_confirm_and_delete(cli):
    code, out, _ = cli("edges", "find", "7203.T")
    assert code == 0 and "2 documents read (0 10-Ks), 2 ties, 1 linked, 1 unconfirmed" in out
    code, out, _ = cli("edges", "show", "7203.T")
    lines = out.splitlines()
    assert "uncalibrated" in lines[0]
    pending = next(line for line in lines if "unconfirmed" in line)
    assert "BWA (BorgWarner Inc.) is a customer (rule customer, Laya none 0.80), news 2026-09-04" in pending
    edge_id = pending.split("]")[0].lstrip("[")
    code, out, _ = cli("edges", "confirm", "7203.T", edge_id)
    assert code == 0 and "BWA is now linked as a customer of 7203.T, first seen 2026-09-04" in out
    code, _, err = cli("edges", "delete", "7203.T", edge_id)
    assert code != 0 and "already confirmed" in err


def test_find_needs_one_of_a_symbol_or_a_list(cli):
    assert cli("edges", "find")[0] == 2
    assert cli("edges", "find", "7203.T", "--list", "main")[0] == 2

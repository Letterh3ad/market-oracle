from datetime import datetime, timedelta, timezone
from functools import partial

import pytest

from oracle.cli import main as cli_main
from oracle.research.laya import MODEL_TAG
from oracle.research.news import Article
from oracle.services.context import load_settings, open_context
from oracle.store.articles import ArticleRepo
from oracle.store.research import ResearchRepo
from tests.fakes.financedatabase import FakeFinanceDatabase, equity
from tests.fakes.google_news import FakeGoogleNews
from tests.fakes.laya import FakeLaya
from tests.fakes.web import FakeWeb
from tests.fakes.yahoo import FakeYahoo

ROWS = [equity("7203.T", "Toyota Motor Corporation", "Japan", "JPX", market_cap="Mega Cap")]


@pytest.fixture
def cli(tmp_path, capsys, monkeypatch):
    cfg = tmp_path / "oracle.toml"
    cfg.write_text("[sources.financedatabase]\nrate_per_second = 1000\n[sources.google_news]\nrate_per_second = 1000\n",
                   encoding="utf-8")
    web = FakeWeb(FakeFinanceDatabase(ROWS), FakeYahoo(), FakeGoogleNews())
    monkeypatch.setattr(cli_main, "open_context", partial(open_context, transport=web.transport))
    monkeypatch.setattr("oracle.services.laya_service.load_scorer", lambda ctx: FakeLaya(tag=MODEL_TAG))

    def run(*argv):
        code = cli_main.main(["--config", str(cfg), *argv])
        out, err = capsys.readouterr()
        return code, out, err

    run("universe", "refresh")
    with open_context(*load_settings(cfg)) as ctx:
        ids = ArticleRepo(ctx.conn).save([Article("https://news.test/1", "Toyota profits rise", "Wire",
                                                  datetime.now(timezone.utc) - timedelta(hours=1))],
                                         "google_news", "2026-09-23T00:00:00Z")
        repo = ResearchRepo(ctx.conn)
        repo.add_evidence(repo.start("7203.T", "ollama", "m", "2026-09-23T00:00:00Z"), ids)
    return run


def test_run_scores_the_companys_news_and_show_prints_its_features_and_events(cli):
    code, out, _ = cli("industry-news", "run", "7203.T")
    assert code == 0 and "1 of 1 companies: 1 searched (1 requests" in out and "1 pairs scored" in out
    code, out, _ = cli("industry-news", "show", "7203.T", "--days", "2")
    assert code == 0 and "uncalibrated" in out
    assert "own" in out and "+0.80" in out and "Toyota profits rise" in out

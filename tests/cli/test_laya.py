from datetime import datetime, timezone
from functools import partial

import pytest

from oracle.cli import main as cli_main
from oracle.research.laya import MODEL_TAG
from oracle.research.news import Article
from oracle.services import laya_service
from oracle.services.context import load_settings, open_context
from oracle.store.articles import ArticleRepo
from oracle.store.research import ResearchRepo
from tests.fakes.financedatabase import UNIVERSE, FakeFinanceDatabase
from tests.fakes.laya import FakeLaya
from tests.fakes.web import FakeWeb
from tests.fakes.yahoo import FakeYahoo


@pytest.fixture
def cli(tmp_path, capsys, monkeypatch):
    cfg = tmp_path / "oracle.toml"
    cfg.write_text("[sources.financedatabase]\nrate_per_second = 1000\n", encoding="utf-8")
    web = FakeWeb(FakeFinanceDatabase(UNIVERSE), FakeYahoo())
    monkeypatch.setattr(cli_main, "open_context", partial(open_context, transport=web.transport))
    monkeypatch.setattr(laya_service, "load_scorer", lambda c: FakeLaya(tag=MODEL_TAG, device="cuda"))

    def run(*argv):
        code = cli_main.main(["--config", str(cfg), *argv])
        out, err = capsys.readouterr()
        return code, out, err

    run("universe", "refresh")
    with open_context(*load_settings(cfg)) as ctx:
        ids = ArticleRepo(ctx.conn).save(
            [Article("https://news.test/1", "Apple beats estimates", "Wire", datetime(2026, 9, 22, tzinfo=timezone.utc)),
             Article("https://news.test/2", "Apple undated", "Wire", None)], "google_news", "2026-09-23T00:00:00Z")
        repo = ResearchRepo(ctx.conn)
        repo.add_evidence(repo.start("AAPL", "ollama", "m", "2026-09-23T00:00:00Z"), ids)
    return run


def test_score_then_show_lists_each_article_newest_first_badged_uncalibrated(cli):
    code, out, _ = cli("laya", "score", "AAPL")
    assert code == 0 and out.splitlines()[-1].startswith("AAPL: 2 scored on cuda, 0 already scored, of 2 articles")
    code, out, _ = cli("laya", "show", "aapl")
    lines = out.splitlines()
    assert code == 0 and lines[0] == "2 articles; probabilities are uncalibrated"
    assert lines[1].startswith("2026-09-23 (fetched)  relevant 0.90  none 0.80  other 0.70  Apple undated")
    assert lines[2].startswith("2026-09-22  relevant 0.90  up   0.80  earnings 0.70  Apple beats estimates")
    assert "0 scored, 2 already scored" in cli("laya", "score", "AAPL")[1]


def test_show_before_any_score_says_how_to_score(cli):
    assert cli("laya", "show", "KO")[1].strip() == "no Laya scores for KO; run `oracle laya score KO`"

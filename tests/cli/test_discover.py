from functools import partial

import pytest

from oracle.cli import main as cli_main
from oracle.services import discovery_service
from oracle.services.context import open_context
from tests.fakes.financedatabase import FakeFinanceDatabase, equity
from tests.fakes.llm import ScriptedModel
from tests.fakes.web import FakeWeb
from tests.fakes.yahoo import FakeYahoo, bar

CARS = dict(currency="USD", sector="Consumer Discretionary", industry_group="Automobiles & Components",
            market_cap="Large Cap")


@pytest.fixture
def run(tmp_path, capsys, monkeypatch):
    cfg = tmp_path / "oracle.toml"
    cfg.write_text("".join(f"[sources.{s}]\nrate_per_second = 1000\n"
                           for s in ("financedatabase", "yahoo", "google_news", "gdelt")), encoding="utf-8")
    yahoo = FakeYahoo()
    names = {"F": "Ford Motor Company", "GM": "General Motors Company", "TSLA": "Tesla, Inc.",
             "STLA": "Stellantis N.V.", "RIVN": "Rivian Automotive, Inc.", "HMC": "Honda Motor Co., Ltd."}
    for s in names:
        yahoo.add(s, "USD", "America/New_York", [bar("2026-09-21", 10.0), bar("2026-09-22", 11.0)])
    web = FakeWeb(FakeFinanceDatabase([equity(s, n, **CARS) for s, n in names.items()]), yahoo)
    monkeypatch.setattr(cli_main, "open_context", partial(open_context, transport=web.transport))
    monkeypatch.setattr(discovery_service, "configured_model", lambda c: ScriptedModel())
    monkeypatch.setattr(discovery_service, "is_stale", lambda last, today: False)

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
    assert "could not use news for F from Google News" in out, "a failed source is said, not fatal"
    assert "GM" in run("watch", "show", "discovered")[1]

from datetime import timedelta

from oracle.cli.main import main
from oracle.config import Config
from oracle.services.context import open_context
from oracle.store.watchlists import WatchlistRepo
from tests.fakes.clock import FakeClock
from tests.services.test_price_factors import NOW, TODAY, rising, store

DAY = TODAY - timedelta(days=5)


def test_factors_prints_the_set_ranked_and_marks_the_unranked(tmp_path, capsys):
    with open_context(Config(db_path=tmp_path / "o.db"), None, FakeClock(NOW)) as c:
        store(c, "UP", rising(0.001))
        store(c, "FLAT", rising(0.0))
        store(c, "YOUNG", rising(0.0), first=TODAY - timedelta(days=100))
        for s in ("UP", "FLAT"):
            WatchlistRepo(c.conn).add("main", s, "2026-09-01T00:00:00Z")
        WatchlistRepo(c.conn).add("new", "YOUNG", "2026-09-01T00:00:00Z")
    cfg = tmp_path / "oracle.toml"
    cfg.write_text('db_path = "o.db"\n', encoding="utf-8")

    assert main(["--config", str(cfg), "factors", "--as-of", DAY.isoformat()]) == 0
    out = capsys.readouterr().out
    assert f"as of {DAY.isoformat()}" in out and "across 3 watched, held and discovered listings (2 with" in out
    lines = [line.split()[0] for line in out.splitlines() if line.startswith("  ") and not line.startswith("  symbol")]
    assert lines[:3] == ["UP", "FLAT", "YOUNG"]
    assert "less than a year of prices" in out

    assert main(["--config", str(cfg), "factors", "--list", "new", "--as-of", DAY.isoformat()]) == 0
    out = capsys.readouterr().out
    assert "new's listings, ranked across 3" in out and "  UP " not in out and "  YOUNG" in out

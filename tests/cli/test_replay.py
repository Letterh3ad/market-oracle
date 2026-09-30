import json
from datetime import date, datetime, timedelta, timezone
from functools import partial

import pytest

from oracle.cli import main as cli_main
from oracle.config import Config
from oracle.services import replay_score
from oracle.services.context import open_context
from oracle.services.prices_service import ListingOutcome
from oracle.store.bars import BarRepo
from oracle.store.meta import MetaRepo
from oracle.store.replay import ReplayRepo
from oracle.store.universe import UniverseRepo
from tests.fakes.cutoff import store_probe
from tests.fakes.web import FakeWeb
from tests.fakes.yahoo import FakeYahoo, bar
from tests.services.test_replay import SPREAD, SYMBOLS
from tests.services.test_replay_score import LISTINGS, bars, replay_run

main = cli_main.main


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    yahoo = FakeYahoo()
    # The CLI runs on the real clock: prices up to today.
    days = [d for d in (date(2026, 5, 1) + timedelta(n) for n in range((date.today() - date(2026, 5, 1)).days))
            if d.weekday() < 5]
    for symbol in SYMBOLS:
        yahoo.add(symbol, "USD", "America/New_York", [bar(d.isoformat(), 10.0) for d in days])
    web = FakeWeb(yahoo)
    monkeypatch.setattr(cli_main, "open_context", partial(open_context, transport=web.transport))
    with open_context(Config(db_path=tmp_path / "o.db"), None) as c:
        UniverseRepo(c.conn).replace_all(SPREAD)
        MetaRepo(c.conn).set("universe_refreshed_at", "2026-09-24T00:00:00Z")
        store_probe(c.conn, c.config)
    path = tmp_path / "oracle.toml"
    path.write_text('db_path = "o.db"\n[sources.yahoo]\nrate_per_second = 1000\n', encoding="utf-8")
    return str(path)


def replay(cfg, *args):
    return main(["--config", cfg, "replay", *args])


def test_create_start_pause_and_status(cfg, capsys):
    assert replay(cfg, "create", "--from", "2026-06-01") == 0
    out = capsys.readouterr().out
    assert "Created replay study 1: 7 listings" in out and "It is paused" in out and "PETR4.SA" in out
    assert "US Information Technology: checking AAPL" in out
    assert "Left out, nothing fits: US Health Care; US Consumer Discretionary;" in out
    assert replay(cfg, "start") == 0
    capsys.readouterr()
    replay(cfg, "status", "--json")
    s = json.loads(capsys.readouterr().out)
    assert (s["state"], s["done"], s["failed"], s["listings"][0]["symbol"]) == ("running", 0, [], "AAPL")
    assert s["first_day"] == "2026-06-01" and s["pending"] == s["total"]
    assert (s["polled_at"], s["finished"], s["complete"], s["left_out"]) == (None, False, False, [])
    assert replay(cfg, "pause") == 0
    capsys.readouterr()
    replay(cfg, "status")
    out = capsys.readouterr().out
    assert "Replay study 1, 2026-06-01" in out and ": paused" in out and "listings: AAPL, JPM" in out
    assert "has not polled yet" in out
    assert "stage models: queries on default (ollama " in out and "), research on default (ollama " in out and "measured cutoff: 2025-05-31" in out


def test_create_picks_a_mid_small_half_too_unless_bands_says_large(cfg, capsys):
    assert replay(cfg, "create", "--from", "2026-06-01") == 0
    out = capsys.readouterr().out
    assert "US Health Care (mid/small)" in out and "a mix checks about twice as many" in out
    assert replay(cfg, "create", "--from", "2026-06-01", "--bands", "large") == 0
    out = capsys.readouterr().out
    assert "Created replay study 2: 7 listings" in out and "(mid/small)" not in out
    assert replay(cfg, "create", "--from", "2026-06-01", "--symbols", "AAPL", "--bands", "mid-small") == 1
    assert "large half" in capsys.readouterr().err


def test_create_with_symbols_then_replace_one(cfg, capsys):
    assert replay(cfg, "create", "--from", "2026-06-01", "--symbols", "AAPL,JPM") == 0
    assert "2 listings" in capsys.readouterr().out
    assert replay(cfg, "replace", "JPM", "HSBA.L") == 0
    assert "Replaced JPM with HSBA.L" in capsys.readouterr().out


def test_without_a_study_status_says_how_to_create_one(cfg, capsys):
    replay(cfg, "status")
    assert "No replay study yet" in capsys.readouterr().out
    replay(cfg, "status", "--json")
    assert capsys.readouterr().out.strip() == "null"


def test_create_on_or_before_the_measured_cutoff_is_refused(cfg, capsys):
    assert replay(cfg, "create", "--from", "2025-05-31") == 1
    assert "on or before the measured cutoff 2025-05-31" in capsys.readouterr().err


def test_retry_all_or_one_failed_cell(cfg, capsys, tmp_path):
    replay(cfg, "create", "--from", "2026-06-01")
    with open_context(Config(db_path=tmp_path / "o.db"), None) as c:
        repo = ReplayRepo(c.conn)
        a, b, d = repo.cells(1)[:3]
        for cell in (a, b, d):
            for _ in range(cell.allowed):
                repo.mark_failed(cell.id, "boom", repo.current().created_at)
    capsys.readouterr()
    assert replay(cfg, "retry", str(b.id)) == 0
    assert "1 failed cell back" in capsys.readouterr().out
    assert replay(cfg, "retry") == 0
    assert "2 failed cells back" in capsys.readouterr().out


def scored_study(tmp_path, fetched=("AAA", "BBB"), bands=None):
    """A study whose windows all closed in 2025 and were fetched whole after, so the report
    needs no fetch whatever today is."""
    days = (date(2025, 6, 2), date(2025, 7, 1))
    with open_context(Config(db_path=tmp_path / "o.db"), None) as c:
        repo = ReplayRepo(c.conn)
        listings = [{**p, "market_cap": (bands or {}).get(p["symbol"], p["market_cap"])} for p in LISTINGS[:2]]
        sid = repo.create(datetime(2025, 8, 1, tzinfo=timezone.utc), *days, json.dumps(listings),
                          [(p["symbol"], d) for d in days for p in LISTINGS[:2]], allowed=1)
        # Entries are the first closes after each as-of day: Jun 3 and Jul 2.
        closes = {"AAA": [100, 100, 110, 110, 121, 121, 121, 121], "BBB": [100, 100, 100, 100, 90, 90, 90, 90]}
        for s, values in closes.items():
            days_closes = dict(zip(("2025-06-02", "2025-06-03", "2025-07-01", "2025-07-02", "2025-08-01",
                                    "2025-09-02", "2025-10-01", "2025-12-31"), values))
            BarRepo(c.conn).upsert(bars(s, days_closes), "2026-01-01T00:00:00Z")
        at = datetime(2025, 8, 1, tzinfo=timezone.utc)
        for cell, call in zip(repo.cells(sid), [("buy", 0.9), ("sell", 0.7), ("buy", 0.8), None]):
            repo.mark_done(cell.id, replay_run(c, cell.symbol, cell.as_of, *(call or ())), at)
        MetaRepo(c.conn).set(f"{replay_score.PRICES_KEY}:{sid}", json.dumps({"fetched": dict.fromkeys(fetched,
                                                                                                    "2026-01-01")}))


@pytest.fixture
def no_fetch(monkeypatch):
    async def fetch(*_):
        raise AssertionError("nothing is missing")

    monkeypatch.setattr(replay_score, "update_listings", fetch)


def test_report_prints_the_score_and_its_limits_without_fetching(cfg, capsys, tmp_path, no_fetch):
    scored_study(tmp_path)
    assert replay(cfg, "report") == 0
    out = capsys.readouterr().out
    assert "3 verdicts, 1 without a verdict, 0 failed, 0 still to run, of 4 replays" in out
    assert "Verdict mix: 2 buy, 0 hold, 1 sell (67% buys), mean confidence 0.80" in out
    assert "1 month: 3 scored, 0 pending, 0 without prices; hit rate 67%, 100% against the basket" in out
    # Called windows: 2 of 3 rose and 2 of 3 beat the basket; two buys and a sell: (2/3 + 2/3 + 1/3) / 3.
    assert ("no-skill baseline on the called windows: 67% rose and 67% beat the basket, so this call mix would "
            "hit 56% (56% against the basket) by chance") in out
    assert "Study prices last checked: 20" in out  # the report's own check just ran
    assert "survivorship" in out and "GDELT" in out
    replay(cfg, "report", "--json")
    body = json.loads(capsys.readouterr().out)
    assert body["verdicts"] == 3 and body["horizons"][0]["horizon"] == "1m" and body["limits"]
    assert [line["name"] for line in body["account"]["lines"]] == [
        "ai", "all_buy", "buy_and_hold", "momentum", "regime", "regime_v2", "core_sat"]


def test_report_prints_the_account_against_buy_and_hold(cfg, capsys, tmp_path, no_fetch):
    scored_study(tmp_path)
    replay(cfg, "report")
    out = capsys.readouterr().out
    assert "Account: the verdicts traded as the AI account would, 2025-06-02 to 2025-12-31, 2 listings" in out
    # Two months of bars before the study are too few for a risk size, so nothing is bought.
    assert "costs (GBP)" in out and "  AI                    +0.00%          +0.00%       0          0.00" in out
    assert "  Every verdict a buy   +0.00%" in out
    assert "  Buy and hold          +5.08%          -0.40%       2      3,994.01" in out  # +5.5% less entry costs
    assert "returns stay in its own currency" in out and "0.5% stamp duty on LSE buys" in out


def test_report_fetches_missing_bars_first_and_says_how_it_went(cfg, capsys, tmp_path, monkeypatch):
    async def fetch(c, symbols, since):
        return [ListingOutcome(s, 0, None, f"no price data for {s}") for s in symbols]

    monkeypatch.setattr(replay_score, "update_listings", fetch)
    scored_study(tmp_path, fetched=("AAA",))
    assert replay(cfg, "report") == 0
    out = capsys.readouterr().out
    assert "Prices for BBB: failed (no price data for BBB)" in out
    assert "Due a whole-window refetch: BBB" in out and "; failed: BBB" in out
    replay(cfg, "report", "--json")
    captured = capsys.readouterr()
    body = json.loads(captured.out)
    assert body["refetch_listings"] == ["BBB"] and body["price_failures"] == ["BBB"] and "BBB" in captured.err


def test_report_says_which_listings_rest_after_repeated_failures(cfg, capsys, tmp_path, monkeypatch):
    async def fetch(c, symbols, since):
        return [ListingOutcome(s, 0, None, f"no price data for {s}") for s in symbols]

    monkeypatch.setattr(replay_score, "update_listings", fetch)
    scored_study(tmp_path, fetched=("AAA",))
    for _ in range(3):
        replay(cfg, "report")
    capsys.readouterr()
    replay(cfg, "report")
    out = capsys.readouterr().out
    assert "Prices for BBB: skipped (failed 3 times in a row: next try after " in out
    assert "; failed: BBB; resting after repeated failures: BBB until " in out


def test_report_without_a_study(cfg, capsys, no_fetch):
    replay(cfg, "report")
    assert "No replay study yet" in capsys.readouterr().out


def test_report_splits_the_headline_numbers_by_size(cfg, capsys, tmp_path, no_fetch):
    scored_study(tmp_path, bands={"BBB": "Small Cap"})
    replay(cfg, "report")
    out = capsys.readouterr().out
    assert "By size, against the whole study's basket: Large AAA; Mid/small BBB" in out
    # Large: AAA's two buys rose 10% each. Mid/small: BBB's sell stayed flat.
    assert "  1 month            calls     hit  no-skill   hit rel  no-skill rel  mean as called" in out
    assert "    All                  3     67%       56%      100%           56%          +6.67%" in out
    assert "    Large                2    100%      100%      100%          100%         +10.00%" in out
    assert "    Mid/small            1      0%      100%      100%          100%" in out
    assert "  Account return          AI  Every verdict a buy  Buy and hold" in out
    assert "    Large             +0.00%               +0.00%       +20.52%" in out
    replay(cfg, "report", "--json")
    body = json.loads(capsys.readouterr().out)
    assert [z["size"] for z in body["by_size"]] == ["large", "mid-small"]
    assert body["by_size"][0]["account"]["listings"] == ["AAA"]

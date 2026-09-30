"""The shared daily aggregator of Laya scores (live tick and 06's history tests alike): per company,
day and relation, point in time."""

from datetime import date, datetime, timezone

import pytest

from oracle.config import Config
from oracle.research.news import Article
from oracle.services.context import open_context
from oracle.services.laya_features import (
    DailyFeature, daily_features, store_features, stored_features, usable_on,
)
from oracle.services.laya_service import score_articles
from oracle.services.universe_service import refresh_universe
from oracle.store.articles import ArticleRepo
from tests.fakes.clock import FakeClock
from tests.fakes.financedatabase import UNIVERSE, FakeFinanceDatabase
from tests.fakes.laya import FakeLaya
from tests.fakes.web import FakeWeb
from tests.fakes.yahoo import FakeYahoo

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)
MODEL = "fake-laya"


@pytest.fixture
async def ctx(tmp_path):
    web = FakeWeb(FakeFinanceDatabase(UNIVERSE), FakeYahoo())
    with open_context(Config(db_path=tmp_path / "o.db"), None, FakeClock(NOW), web.transport) as c:
        await refresh_universe(c)
        yield c


def stored(ctx, title, published, fetched="2026-09-30T08:00:00+00:00"):
    [i] = ArticleRepo(ctx.conn).save([Article(f"https://n.test/{title}", title, "Wire", published)], "google_news",
                                     fetched)
    return i


def at(day, hour=9, minute=0):
    return datetime(2026, 9, day, hour, minute, tzinfo=timezone.utc)


def score(ctx, relation, *ids, symbol="AAPL"):
    score_articles(ctx, symbol, list(ids), relation=relation, scorer=FakeLaya())


def test_a_day_sums_direction_times_its_probability_per_relation_and_counts_event_types(ctx):
    score(ctx, "own", stored(ctx, "Apple beats estimates", at(28)), stored(ctx, "Apple recall of chargers", at(28)),
          stored(ctx, "Apple opens a store", at(28)))
    score(ctx, "competitor", stored(ctx, "Samsung profits rise", at(28)))
    rows = daily_features(ctx, "AAPL", date(2026, 9, 1), date(2026, 9, 30), model=MODEL)
    assert rows == [
        DailyFeature("AAPL", date(2026, 9, 28), "own", 3, 0.0, {"earnings": 1, "recall_safety": 1, "other": 1}),
        DailyFeature("AAPL", date(2026, 9, 28), "competitor", 1, 0.8, {"other": 1}),
    ], "up +0.8, down -0.8, none 0; own before competitor"


def test_an_article_counts_on_the_utc_day_it_was_known_and_acts_only_from_the_next_session(ctx):
    late = stored(ctx, "Apple beats late", datetime(2026, 9, 29, 23, 30, tzinfo=timezone.utc))
    score(ctx, "own", late)
    [row] = daily_features(ctx, "AAPL", date(2026, 9, 1), date(2026, 9, 30), model=MODEL)
    assert row.day == date(2026, 9, 29)
    assert usable_on([row], date(2026, 9, 29)) == [], "never on the day it was known"
    assert usable_on([row], date(2026, 9, 30)) == [row]


def test_an_undated_article_counts_on_its_fetch_day_live_and_never_in_history(ctx):
    score(ctx, "own", stored(ctx, "Apple beats undated", None, fetched="2026-09-27T06:00:00+00:00"))
    [row] = daily_features(ctx, "AAPL", date(2026, 9, 1), date(2026, 9, 30), model=MODEL)
    assert row.day == date(2026, 9, 27)
    assert daily_features(ctx, "AAPL", date(2026, 9, 1), date(2026, 9, 30), model=MODEL, history=True) == []


def test_the_range_is_inclusive_and_nothing_known_later_leaks_into_it(ctx):
    score(ctx, "own", stored(ctx, "Apple beats a", at(25)), stored(ctx, "Apple beats b", at(26)),
          stored(ctx, "Apple beats c", at(27, 0, 0)))
    days = [r.day.day for r in daily_features(ctx, "AAPL", date(2026, 9, 25), date(2026, 9, 26), model=MODEL)]
    assert days == [25, 26]


def test_a_relation_override_recounts_or_drops_each_score(ctx):
    a, b = stored(ctx, "Foxconn rise", at(28)), stored(ctx, "Qualcomm rise", at(28))
    score(ctx, "supplier", a, b)
    rows = daily_features(ctx, "AAPL", date(2026, 9, 28), date(2026, 9, 28), model=MODEL,
                          relation_for=lambda s: None if s.title.startswith("Qualcomm") else "industry")
    assert [(r.relation, r.articles) for r in rows] == [("industry", 1)]


def test_features_are_stored_per_day_and_a_rerun_restates_the_days_it_covers(ctx):
    a = stored(ctx, "Apple beats", at(28))
    score(ctx, "own", a)
    assert store_features(ctx, "AAPL.MI", date(2026, 9, 27), date(2026, 9, 30), model=MODEL) == 1
    assert stored_features(ctx, "AAPL", date(2026, 9, 1), date(2026, 9, 30), model=MODEL) == daily_features(
        ctx, "AAPL", date(2026, 9, 1), date(2026, 9, 30), model=MODEL), "any listing stores under the primary"
    ctx.conn.execute("DELETE FROM laya_scores")
    assert store_features(ctx, "AAPL", date(2026, 9, 27), date(2026, 9, 30), model=MODEL) == 0
    assert stored_features(ctx, "AAPL", date(2026, 9, 1), date(2026, 9, 30), model=MODEL) == []

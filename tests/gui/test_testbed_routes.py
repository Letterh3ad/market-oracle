from datetime import date

from oracle.market.bars import Bar
from oracle.services.testbed_service import research_context
from oracle.store.bars import BarRepo
from oracle.store.testbed import Snapshot, TestbedRepo


def seed(ctx):
    with research_context(ctx) as r:
        TestbedRepo(r.conn).save_snapshot(Snapshot("fja05680/sp500", "https://x", "abc1234", "2026-09-23T00:00:00+00:00",
                                                   date(2010, 1, 4), date(2010, 1, 4), 1),
                                          [(date(2010, 1, 4), ("AAA", "BBB"))])
        BarRepo(r.conn).upsert([Bar(s, date(2010, 1, 4), 1, 1, 1, 1, 1, 1, "USD", "yahoo") for s in ("SPY", "AAA")],
                               "2026-09-23T00:00:00+00:00")


async def test_coverage_is_empty_before_a_build(loaded):
    body = (await loaded.get("/api/testbed/coverage")).json()
    assert body["years"] == [] and body["under_priced"] == [] and body["snapshot"] is None and body["last_run"] is None


async def test_coverage_shows_the_missing_share_per_year_and_the_unpriced(loaded, ctx):
    seed(ctx)
    body = (await loaded.get("/api/testbed/coverage")).json()
    assert body["years"][0] == {"year": 2010, "members": 2, "member_days": 2, "with_bars": 1, "unpriced": 1,
                                "missing_share": 0.5}
    assert body["under_priced"] == [{"ticker": "BBB", "first_day": "2010-01-04", "last_day": "2010-01-04",
                                     "member_days": 1, "with_bars": 0, "note": "not fetched yet"}]
    assert body["duplicates"] == [] and body["under_priced_below"] == 0.5 and body["as_of"] == "2026-09-23"
    assert body["snapshot"]["revision"] == "abc1234" and body["calendar"] == "SPY"


async def test_build_is_queued_as_one_job_and_pressed_again_joins_it(loaded):
    first = await loaded.post("/api/jobs/testbed")
    again = await loaded.post("/api/jobs/testbed")
    refresh = await loaded.post("/api/jobs/testbed?refresh=true")
    assert first.status_code == 202 and first.json()["kind"] == "testbed"
    assert again.json()["id"] == first.json()["id"] and refresh.json()["id"] != first.json()["id"]

from datetime import date

from oracle.services.trend_service import SERIES, run_and_keep_trend
from tests.services.test_trend_overlay_service import NY, bars, weekdays


async def test_nothing_kept_reads_as_none(client):
    assert (await client.get("/api/plan/trend")).json() == {"result": None}


async def test_the_test_is_one_job_at_a_time(client):
    first = await client.post("/api/jobs/plan-trend")
    again = await client.post("/api/jobs/plan-trend")
    assert first.status_code == 202 and first.json()["kind"] == "plan_trend"
    assert again.json()["id"] == first.json()["id"] and first.json()["label"] == "Trend overlay test"


async def test_the_kept_result_has_both_windows_as_compare_lines_and_the_verdict(client, ctx, yahoo):
    yahoo.add(SERIES.symbol, "USD", NY, bars(weekdays(date(1988, 1, 4), date(2012, 12, 31))), kind="INDEX")
    kept = await run_and_keep_trend(ctx)
    body = (await client.get("/api/plan/trend")).json()["result"]
    assert body["id"] == kept.id and body["verdict"] == kept.verdict and body["series"] == "^SP500TR"
    confirm = body["confirm"]
    assert [l["name"] for l in confirm["lines"]] == ["trend_overlay", "held"]
    trend = confirm["lines"][0]
    assert trend["return"] == kept.confirm.trend.ret and trend["trades"] == kept.confirm.switches
    assert trend["points"][0] == ["2008-01-01", 0.0]
    assert body["choose"]["verdict"] is None and confirm["verdict"] in ("PASS", "FAIL")
    assert confirm["crashes"][0]["name"] == "2008" and body["cash"].startswith("0:")

from oracle.services.fund_compare_service import run_and_keep_funds
from tests.services.test_fund_compare_service import market


async def test_nothing_kept_reads_as_none(client):
    assert (await client.get("/api/plan/funds")).json() == {"result": None}


async def test_the_comparison_is_one_job_at_a_time(client):
    first = await client.post("/api/jobs/plan-funds")
    again = await client.post("/api/jobs/plan-funds")
    assert first.status_code == 202 and first.json()["kind"] == "plan_funds"
    assert again.json()["id"] == first.json()["id"] and first.json()["label"] == "Fund comparison"


async def test_the_kept_result_has_each_fund_as_a_compare_line_and_the_tracking_rows(client, ctx, yahoo):
    market(yahoo)
    kept = await run_and_keep_funds(ctx)
    body = (await client.get("/api/plan/funds")).json()["result"]
    assert body["id"] == kept.id and body["base"] == "GBP" and body["start"] == "2018-03-01"
    first = body["rows"][0]
    assert first["name"] == "all_world" and first["return"] == kept.rows[0].ret and first["trades"] == 0
    assert first["points"][0] == ["2018-03-01", 0.0] and "proxy" in first["label"]
    assert [t["real"] for t in body["tracking"]] == ["VWRP.L", "SWDA.L", "VUAG.L", "VUKG.L"]
    assert body["fx"]["symbol"] == "ACWI"

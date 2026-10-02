from dataclasses import replace

from oracle.config import PlanConfig
from tests.services.test_plan_money import store


async def test_fees_returns_every_platform_line_and_the_crossover(client, ctx):
    ctx.config = replace(ctx.config, plan=PlanConfig(percent_fee=0.25, flat_fee=4.99))
    body = (await client.get("/api/plan/fees", params={"monthly": 500, "years": 20, "growth": 5})).json()
    assert [line["name"] for line in body["lines"]] == ["none", "percentage", "flat"]
    assert round(body["crossover"]) == 23_952 and body["growth"] == 0.05 and body["using"] == "percentage"
    assert len(body["lines"][1]["yearly"]) == 20


async def test_fees_refuses_nonsense_with_a_message(client):
    r = await client.get("/api/plan/fees", params={"monthly": 100, "years": 0})
    assert r.status_code == 400 and "years" in r.json()["error"]


async def test_contributions_returns_the_starts_spreads_mix_and_cash(client, ctx):
    store(ctx, "ACWI", [100.0 * 1.01 ** i for i in range(40)])
    store(ctx, "CUKX.L", [100.0] * 40)
    body = (await client.get("/api/plan/contributions", params={"monthly": 100, "years": 2, "second": "cukx.l"})).json()
    assert len(body["starts"]) == 28 and body["starts"][0][0] == "2015-01-25"
    assert body["lump"]["count"] == 28 and body["window_count"] == 16
    assert [m["rule"] for m in body["mixes"]] == ["none", "yearly", "5-point bands"]
    assert "path" not in body["mixes"][0]["result"]
    assert body["cash"]["share"] == 0.05


async def test_contributions_without_prices_say_so(client):
    r = await client.get("/api/plan/contributions", params={"monthly": 100})
    assert r.status_code >= 400 and "ACWI" in r.json()["error"]

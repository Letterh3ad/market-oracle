from dataclasses import replace

import pytest

from oracle.config import PlanConfig
from tests.services.test_plan_money import seed


async def test_fees_returns_every_platform_line_and_the_crossover(client, ctx):
    ctx.config = replace(ctx.config, plan=PlanConfig(percent_fee=0.25, flat_fee=4.99))
    body = (await client.get("/api/plan/fees", params={"monthly": 500, "years": 20, "growth": 5})).json()
    assert [line["name"] for line in body["lines"]] == ["none", "percentage", "flat"]
    assert round(body["crossover"]) == 23_952 and body["growth"] == 0.05 and body["using"] == "percentage"
    assert len(body["lines"][1]["yearly"]) == 20


async def test_fees_refuses_nonsense_with_a_message(client):
    r = await client.get("/api/plan/fees", params={"monthly": 100, "years": 0})
    assert r.status_code == 400 and "years" in r.json()["error"]


@pytest.fixture
async def seeded(ctx, tmp_path):
    ctx.config = replace(ctx.config, research_db_path=tmp_path / "r.db")
    await seed(tmp_path)


async def test_contributions_return_the_span_starts_spreads_mix_and_cash(client, seeded):
    body = (await client.get("/api/plan/contributions", params={"monthly": 100, "years": 2, "second": "UK"})).json()
    assert (body["first"], body["last"], body["months"]) == ("2018-01-31", "2020-12-31", 35)
    assert len(body["starts"]) == 24 and body["start_span"] == ["2018-01-31", "2019-12-31"]
    assert body["window_count"] == 12 and body["window_span"] == ["2018-01-31", "2018-12-31"]
    assert body["series"]["key"] == "all_world" and body["second"]["key"] == "uk"
    assert [m["rule"] for m in body["mixes"]] == ["none", "yearly", "5-point bands"]
    assert "path" not in body["mixes"][0]["result"]
    assert body["cash"]["share"] == 0.05


@pytest.mark.parametrize("params", [{"monthly": 100, "lump": 0}, {"monthly": 100, "lump": 60, "years": 1}])
async def test_a_zero_lump_or_fees_eating_the_drip_are_a_clean_400(client, ctx, seeded, params):
    ctx.config = replace(ctx.config, plan=PlanConfig(dealing_fee=10.0))
    r = await client.get("/api/plan/contributions", params=params)
    assert r.status_code == 400 and r.json()["error"]


async def test_contributions_without_history_say_which_job_to_run(client):
    r = await client.get("/api/plan/contributions", params={"monthly": 100})
    assert r.status_code >= 400 and "fund comparison" in r.json()["error"]


PLATFORM = {"platform": "flat", "percent_fee": 0.25, "percent_cap": 0, "flat_fee": 4.99, "dealing_fee": 0,
            "fx_fee": 0}


async def test_the_platform_saves_in_one_write(client, ctx):
    r = await client.put("/api/plan/platform", json=PLATFORM)
    assert r.status_code == 200 and r.json()["flat_fee"] == 4.99
    assert ctx.config.plan.platform == "flat"
    assert "flat_fee = 4.99" in ctx.config_source.read_text(encoding="utf-8")


async def test_a_refused_platform_value_saves_nothing(client, ctx):
    await client.put("/api/plan/platform", json=PLATFORM)
    r = await client.put("/api/plan/platform", json={**PLATFORM, "flat_fee": 9.99, "fx_fee": 500})
    assert r.status_code == 400 and "fx_fee" in r.json()["error"]
    assert ctx.config.plan.flat_fee == 4.99
    assert "flat_fee = 4.99" in ctx.config_source.read_text(encoding="utf-8")


async def test_the_summary_lists_each_line_and_what_is_missing(client):
    body = (await client.get("/api/plan/summary")).json()
    assert [line["name"] for line in body["lines"]] == ["fund", "wrapper", "platform", "monthly", "overlay", "rule"]
    assert body["fund"] == "all_world" and body["picked"] is False and body["overlay"] is False
    assert [m["step"] for m in body["missing"]] == ["funds", "platform", "monthly", "trend", "rule"]
    assert ["sp500", "S&P 500"] in body["funds"]


async def test_the_choice_saves_fund_and_monthly_only_and_refuses_an_unknown_fund(client, ctx):
    await client.put("/api/plan/platform", json=PLATFORM)
    r = await client.put("/api/plan/choice", json={"fund": "sp500", "monthly": 400})
    assert r.status_code == 200 and r.json()["fund"] == "sp500" and r.json()["monthly"] == 400
    assert ctx.config.plan.flat_fee == 4.99 and ctx.config.plan.platform == "flat"
    r = await client.put("/api/plan/choice", json={"fund": "gold"})
    assert r.status_code == 400 and "gold" in r.json()["error"]
    r = await client.put("/api/plan/choice", json={"fund": "default"})
    assert r.status_code == 200 and r.json()["pending"]["changes"] == {"fund": ["sp500", ""]}
    assert ctx.config.plan.fund == "sp500" and ctx.config.plan.monthly == 400, "a set fund changes after 7 days"
    assert (await client.put("/api/plan/choice", json={"monthly": "lots"})).status_code in (400, 422)

from datetime import timedelta

import pytest

from oracle.services import guardrails_service
from oracle.services.errors import NoPrices
from tests.services.test_drawdown_preview import SERIES

PLATFORM = {"platform": "percentage", "percent_fee": 0.25, "percent_cap": 0, "flat_fee": 4.99, "dealing_fee": 0,
            "fx_fee": 0}


async def test_a_first_rule_applies_at_once_and_a_change_waits_until_cancelled(client, ctx):
    r = (await client.put("/api/plan/rule", json={"rule": " Never sell in a fall. "})).json()
    assert r["rule"] == "Never sell in a fall." and r["applied"] == ["rule"] and r["pending"] is None
    r = (await client.put("/api/plan/rule", json={"rule": "Sell when scared."})).json()
    assert r["rule"] == "Never sell in a fall." and r["pending"]["changes"] == {"rule": ["Never sell in a fall.",
                                                                                         "Sell when scared."]}
    g = (await client.get("/api/plan/guardrails")).json()
    assert g["rule"] == "Never sell in a fall." and g["cool_off_days"] == 7 and g["pending"]["state"] == "pending"
    assert (await client.delete("/api/plan/pending")).json()["state"] == "cancelled"
    g = (await client.get("/api/plan/guardrails")).json()
    assert g["pending"] is None and g["history"][0]["state"] == "cancelled"
    r = await client.delete("/api/plan/pending")
    assert r.status_code == 400 and "nothing pending" in r.json()["error"]


async def test_a_platform_change_waits_and_the_summary_takes_it_once_due(client, ctx, clock):
    await client.put("/api/plan/platform", json=PLATFORM)
    r = (await client.put("/api/plan/platform", json={**PLATFORM, "percent_fee": 0.15})).json()
    assert r["percent_fee"] == 0.25 and r["pending"]["changes"] == {"percent_fee": [0.25, 0.15]}
    clock.advance(timedelta(days=7).total_seconds())
    body = (await client.get("/api/plan/summary")).json()
    assert ctx.config.plan.percent_fee == 0.15
    assert "0.15%" in next(line["value"] for line in body["lines"] if line["name"] == "platform")


async def test_drawdowns_answer_each_crash_for_the_given_pot(client, monkeypatch):
    monkeypatch.setattr(guardrails_service, "fund_series", lambda ctx, key, monthly=False: SERIES[key])
    body = (await client.get("/api/plan/drawdowns", params={"pot": 20000})).json()
    assert [r["crash"]["name"] for r in body["rows"]] == ["2000-02", "2008-09", "2020", "2022"]
    assert body["pot"] == 20000 and body["example"] is False
    assert round(next(r for r in body["rows"] if r["crash"]["name"] == "2020")["pot_loss"]) == -5000


async def test_drawdowns_without_history_say_which_job_to_run(client, monkeypatch):
    def none(ctx, key, monthly=False):
        raise NoPrices("none")

    monkeypatch.setattr(guardrails_service, "fund_series", none)
    r = await client.get("/api/plan/drawdowns")
    assert r.status_code >= 400 and "Compare funds" in r.json()["error"]


@pytest.mark.parametrize("path", ["/api/plan/fees?monthly=100", "/api/plan/drawdowns",
                                  "/api/plan/contributions?monthly=100", "/api/plan/guardrails"])
async def test_every_plan_reader_takes_a_due_change_first(client, ctx, clock, monkeypatch, path):
    monkeypatch.setattr(guardrails_service, "fund_series", lambda ctx, key, monthly=False: SERIES[key])
    await client.put("/api/plan/platform", json=PLATFORM)
    await client.put("/api/plan/platform", json={**PLATFORM, "percent_fee": 0.15})
    clock.advance(timedelta(days=7).total_seconds())
    await client.get(path)
    assert ctx.config.plan.percent_fee == 0.15


async def test_the_platform_form_may_send_only_the_edited_fields(client, ctx):
    await client.put("/api/plan/platform", json=PLATFORM)
    r = (await client.put("/api/plan/platform", json={"flat_fee": 3.99})).json()
    assert r["pending"]["changes"] == {"flat_fee": [4.99, 3.99]} and ctx.config.plan.percent_fee == 0.25

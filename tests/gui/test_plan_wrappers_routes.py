from dataclasses import replace

from oracle.config import PlanConfig


async def test_wrappers_with_nothing_entered_list_what_is_missing(client):
    body = (await client.get("/api/plan/wrappers")).json()
    assert body["lisa"] is False and body["rows"] == [] and body["tax_year"] == "2026-27"
    assert [m["step"] for m in body["missing"]] == ["salary", "born", "needed", "monthly"]
    assert all(url.startswith("https://") for _, url in body["sources"])


async def test_saving_numbers_and_the_lisa_switch_then_reading_the_comparison(client, ctx):
    ctx.config = replace(ctx.config, plan=PlanConfig(monthly=500))
    r = await client.put("/api/plan/wrappers", json={"salary": 30000, "employee_pct": 5, "employer_pct": 3,
                                                     "born": 1996, "needed_before": 100, "lisa": True})
    assert r.status_code == 200 and r.json()["lisa"] is True and ctx.config.wrappers.salary == 30000
    assert "lisa = true" in ctx.config_source.read_text(encoding="utf-8")
    body = (await client.get("/api/plan/wrappers", params={"growth": 4})).json()
    assert [row["key"] for row in body["rows"]] == ["isa", "pension", "lisa"] and body["growth"] == 0.04
    assert all("per_pound" in row for row in body["rows"]) and body["steps"][0]["wrapper"] == "pension"
    assert (await client.put("/api/plan/wrappers", json={"lisa": False})).json()["lisa"] is False


async def test_bad_values_are_a_clean_400(client, ctx):
    for body in ({"employee_pct": 150}, {"lisa": "yes"}, {"born": 1995.5}):
        r = await client.put("/api/plan/wrappers", json=body)
        assert r.status_code in (400, 422), body
    assert ctx.config.wrappers.employee_pct == 0
    r = await client.get("/api/plan/wrappers", params={"growth": 90})
    assert r.status_code == 400 and "growth" in r.json()["error"]

"""The Research tab's scoreboard and registry routes."""

import threading

from oracle.services.lab_registry import registration

TEMPLATE = {"key": "new_signal", "label": "A new signal", "kind": "signal", "horizons": [20], "rule": "the rule",
            "universe": "the testbed", "expected_sign": "positive", "expected_size": "IC 0.02", "source": "a paper",
            "power": "0.01", "costs": "none", "kill_stage": "confirm"}


async def test_the_scoreboard_lists_the_legacy_tests_with_their_own_bars(client):
    body = (await client.get("/api/lab/scoreboard")).json()
    rows = {(r["key"], r["horizon"]): r for r in body["rows"]}
    assert body["n"] == 19 and round(body["next_bar"], 4) == 3.0233
    assert rows[("momentum_12_1", 20)]["bar"] == "t > 2.00" and rows[("momentum_12_1", 20)]["verdict"] == "not run"
    assert rows[("trend_overlay", None)]["kind"] == "strategy"


async def test_register_show_commit_and_drop(client):
    made = await client.post("/api/lab/tests", json=TEMPLATE)
    assert made.status_code == 200 and made.json()["bar"] == 3.0233 and made.json()["commit_hash"] is None
    shown = (await client.get("/api/lab/tests/new_signal")).json()
    assert shown["bar_text"] == "t >= 3.02" and shown["rule"] == "the rule"
    bad = await client.put("/api/lab/tests/new_signal/commit", json={"hash": "not a hash"})
    assert bad.status_code == 400 and "commit hash" in bad.json()["error"]
    assert (await client.post("/api/lab/tests", json={**TEMPLATE, "horizons": [5, 20, 60]})).status_code == 400
    assert (await client.delete("/api/lab/tests/new_signal")).json() == {"dropped": "new_signal"}
    assert (await client.get("/api/lab/tests/new_signal")).status_code == 404
    refused = await client.delete("/api/lab/tests/momentum_12_1")
    assert refused.status_code == 400 and "counts toward N" in refused.json()["error"]


async def test_a_commit_check_runs_git_off_the_event_loop(client, monkeypatch):
    threads = []

    def check(ctx, key, commit_hash):
        threads.append(threading.current_thread())
        return registration(ctx, key)
    monkeypatch.setattr("oracle.gui.routes.lab.commit_test", check)
    assert (await client.put("/api/lab/tests/momentum_12_1/commit", json={"hash": "abcdef1"})).status_code == 200
    assert threads and threads[0] is not threading.main_thread()


async def test_a_strategy_registers_with_its_variants_tried(client):
    body = {**TEMPLATE, "key": "new_strategy", "kind": "strategy", "horizons": [], "variants": 4}
    made = (await client.post("/api/lab/tests", json=body)).json()
    assert made["variants"] == 4 and "4 variants tried" in made["bar_rule"]


async def test_the_cost_model_and_a_hand_trade(client):
    model = (await client.get("/api/lab/costs")).json()
    assert model["trade"] is None and model["model"]["stamp_duty"] == 0.005 and model["model"]["pot"] == 20000
    uk = (await client.get("/api/lab/costs", params={"value": 10000, "uk_shares": "true"})).json()["trade"]
    assert uk["stamp"] == 50 and uk["fx"] == 0 and uk["spread"] == 10 and uk["total"] == 60
    sell = (await client.get("/api/lab/costs", params={"value": 10000, "uk_shares": "true", "side": "sell"})).json()
    assert sell["trade"]["stamp"] == 0
    bad = await client.get("/api/lab/costs", params={"value": 10, "side": "short"})
    assert bad.status_code == 400 and "buy or sell" in bad.json()["error"]


async def test_strategies_and_a_refused_strategy_job(client):
    assert (await client.get("/api/lab/strategies")).json() == {"strategies": [], "kept": []}
    refused = await client.post("/api/jobs/strategy-test", params={"key": "nope"})
    assert refused.status_code == 400 and "no strategy code" in refused.json()["error"]


async def test_the_trend_check_needs_a_kept_result(client):
    r = await client.get("/api/lab/trend-check")
    assert r.status_code in (404, 409, 400) and "nothing to reproduce" in r.json()["error"]

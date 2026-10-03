"""The Research tab's scoreboard and registry routes."""

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

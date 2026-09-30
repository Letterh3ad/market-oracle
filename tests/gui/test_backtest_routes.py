import pytest

from oracle.services.watch_service import watch
from tests.gui.conftest import DAYS
from tests.gui.jobs import run_job

START, END = DAYS[150].isoformat(), DAYS[-1].isoformat()


@pytest.fixture
async def watched(ctx, priced):
    """AAPL's prices stored and AAPL on the main list."""
    await run_job(priced, "/api/jobs/prices", symbol="AAPL")
    watch(ctx, "AAPL")
    return priced


async def test_the_regime_ai_line_runs_only_when_asked(watched):
    job = await run_job(watched, "/api/jobs/backtest", watchlist="main", start=START, end=END, regime_ai="true")
    assert job["state"] == "done", job
    assert job["label"].endswith(", with regime AI")
    assert [line["name"] for line in job["result"]["lines"]][-1] == "regime_ai"


async def test_the_span_names_the_days_a_backtest_can_cover(watched):
    body = (await watched.get("/api/backtests/span", params={"watchlist": "MAIN"})).json()
    assert body == {"watchlist": "main", "first": DAYS[0].isoformat(), "last": DAYS[-1].isoformat(),
                    "base_currency": "GBP"}


async def test_a_list_without_prices_has_no_span(ctx, loaded):
    watch(ctx, "AAPL")
    body = (await loaded.get("/api/backtests/span", params={"watchlist": "main"})).json()
    assert (body["first"], body["last"]) == (None, None)


async def test_a_backtest_runs_as_a_job_and_is_kept_for_later(watched):
    job = await run_job(watched, "/api/jobs/backtest", watchlist="main", start=START, end=END, slippage_bps=5,
                        commission=1)
    assert (job["kind"], job["state"], job["link"]) == ("backtest", "done", "#ledgers")
    summary = job["result"]
    assert (summary["watchlist"], summary["start"], summary["end"]) == ("main", START, END)
    assert (summary["slippage_bps"], summary["commission"], summary["base_currency"]) == (5, 1, "GBP")
    assert [line["name"] for line in summary["lines"]] == ["strategy", "buy_and_hold", "momentum", "regime",
                                                            "regime_v2", "core_sat"]
    assert "points" not in summary["lines"][0]

    kept = (await watched.get("/api/backtests")).json()
    assert kept == {"runs": [summary], "keep": 10}

    full = (await watched.get(f"/api/backtests/{summary['id']}")).json()
    assert full["listings"] == ["AAPL"]
    strategy, held, momentum, regime, v2, core_sat = full["lines"]
    assert v2["label"] == "Regime-v2" and v2["points"][0] == [START, 0.0]
    assert held["points"][0] == [START, 0.0] and held["return"] == held["points"][-1][1] > 0
    assert strategy["caption"].startswith("Strategy") and momentum["label"] == "Momentum"
    assert regime["label"] == "Regime" and "no AI verdicts" in regime["caption"] and regime["points"][0] == [START, 0.0]
    assert all({"day", "symbol", "side", "shares", "price", "currency", "cash", "costs", "reason"} <= t.keys()
               for t in full["trades"])
    assert any("survivorship" in n for n in full["notes"])
    assert full["walk_forward"] is None


async def test_a_walk_forward_backtest_keeps_its_segment_table_and_fits(watched):
    job = await run_job(watched, "/api/jobs/backtest", watchlist="main", start=START, end=END, walk_forward="true")
    assert job["state"] == "done", job.get("error")
    assert job["label"].endswith("walk-forward")
    full = (await watched.get(f"/api/backtests/{job['result']['id']}")).json()
    wf = full["walk_forward"]
    assert wf["months"] == 6 and len(wf["segments"]) == 1 and wf["segments"][0][0] == START
    assert [x["name"] for x in wf["lines"]] == [x["name"] for x in full["lines"]][:7]
    assert full["lines"][-1]["name"] == "regime_wf"
    held = wf["lines"][1]
    assert held["beat"] is None and held["segments"][0]["excess"] == 0.0
    assert {"ret", "max_fall", "sharpe", "per_fall", "excess"} <= held["overall"].keys()
    assert len(wf["fits"]) == 1 and set(wf["fits"][0][1]) == {"calm_up", "volatile_up", "calm_down", "volatile_down"}


async def test_a_walk_forward_too_short_for_a_segment_is_refused(watched):
    response = await watched.post("/api/jobs/backtest", params={
        "watchlist": "main", "start": START, "end": DAYS[200].isoformat(), "walk_forward": "true"})
    assert response.status_code == 400 and "walk-forward" in response.json()["error"]


@pytest.mark.parametrize(("params", "status", "says"), [
    ({"start": END, "end": START}, 400, "must be before"),
    ({"start": "2000-01-03", "end": END}, 400, "stored from"),
    ({"start": START, "end": END, "slippage_bps": -1}, 400, "slippage"),
    ({"watchlist": "nope", "start": START, "end": END}, 404, "no watchlist"),
    ({"start": "someday", "end": END}, 400, "start"),
])
async def test_bad_requests_are_refused_before_anything_is_queued(watched, params, status, says):
    response = await watched.post("/api/jobs/backtest", params={"watchlist": "main", **params})
    assert response.status_code == status and says in response.json()["error"]
    assert [j for j in (await watched.get("/api/activity")).json()["jobs"] if j["kind"] == "backtest"] == []


async def test_a_run_no_longer_kept_is_not_found(loaded):
    response = await loaded.get("/api/backtests/99")
    assert response.status_code == 404 and "only the last 10" in response.json()["error"]

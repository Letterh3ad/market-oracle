from oracle.services.signal_test_service import keep_signal_test, run_signal_test
from tests.services.test_signal_tests import REGISTRY, seed


async def test_signals_lists_the_registry_with_no_result_before_a_run(loaded):
    body = (await loaded.get("/api/signals")).json()
    assert [(s["name"], s["horizons"], s["result"]) for s in body["signals"]] == [
        ("momentum_12_1", [20], None), ("reversal_1m", [5], None)]


async def test_signals_shows_each_ones_newest_kept_result(loaded, ctx, monkeypatch):
    seed(ctx)
    keep_signal_test(ctx, await run_signal_test(ctx, "null", registry=REGISTRY))
    kept = keep_signal_test(ctx, await run_signal_test(ctx, "future", registry=REGISTRY))
    monkeypatch.setattr("oracle.gui.routes.testbed.SIGNALS", REGISTRY)
    body = (await loaded.get("/api/signals")).json()
    future = next(s for s in body["signals"] if s["name"] == "future")
    assert future["result"]["id"] == kept.id and future["result"]["verdict"] == "PASS"
    assert future["result"]["confirm"][0]["power"] > 0 and future["result"]["confirm"][0]["missing_share"] == 0.0


async def test_a_test_is_queued_as_a_job_and_an_unknown_signal_is_refused(loaded):
    first = await loaded.post("/api/jobs/signal-test?name=reversal_1m")
    again = await loaded.post("/api/jobs/signal-test?name=reversal_1m")
    assert first.status_code == 202 and first.json()["kind"] == "signal_test"
    assert again.json()["id"] == first.json()["id"]
    bad = await loaded.post("/api/jobs/signal-test?name=nope")
    assert bad.status_code == 400 and "no signal 'nope'" in bad.text


async def test_a_train_only_test_is_its_own_job_and_shows_beside_the_full_result(loaded, ctx, monkeypatch):
    first = await loaded.post("/api/jobs/signal-test?name=reversal_1m")
    train = await loaded.post("/api/jobs/signal-test?name=reversal_1m&train_only=true")
    assert train.status_code == 202 and train.json()["id"] != first.json()["id"]
    assert train.json()["label"] == "Signal test: reversal_1m, train only"
    seed(ctx)
    keep_signal_test(ctx, await run_signal_test(ctx, "future", registry=REGISTRY, train_only=True))
    monkeypatch.setattr("oracle.gui.routes.testbed.SIGNALS", REGISTRY)
    future = next(s for s in (await loaded.get("/api/signals")).json()["signals"] if s["name"] == "future")
    assert future["result"] is None and future["train_result"]["verdict_text"] == "TRAIN ONLY"
    assert future["train_result"]["train"][0]["verdict_text"] == "PASS"

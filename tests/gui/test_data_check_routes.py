from oracle.gui.app import build_app
from oracle.services import activity_service
from oracle.services.data_check_service import run_data_checks
from oracle.services.data_checks import CHECKS
from tests.services.test_data_check_service import clean, seed_live


async def test_nothing_run_reads_as_none_with_the_settings(client):
    body = (await client.get("/api/data/checks")).json()
    assert body == {"run": None, "nightly": True, "explain": False}


async def test_the_checks_are_one_job_at_a_time(client):
    first = await client.post("/api/jobs/data-check")
    again = await client.post("/api/jobs/data-check")
    assert first.status_code == 202 and first.json()["kind"] == "data_check" and first.json()["label"] == "Data checks"
    assert again.json()["id"] == first.json()["id"]


async def test_the_kept_run_has_counts_per_check_and_the_flagged_rows(client, ctx):
    seed_live(ctx, [("VUSA.L", "Vanguard S&P 500 UCITS ETF", "etf", "vusa")], clean("VUSA.L"))
    kept = await run_data_checks(ctx)
    run = (await client.get("/api/data/checks")).json()["run"]
    assert [c["code"] for c in run["checks"]] == list(CHECKS) and run["total"] == kept.total
    junk = next(c for c in run["checks"] if c["code"] == "junk_ticker")
    assert junk["count"] == 1 and junk["label"] == CHECKS["junk_ticker"]
    assert junk["rows"][0]["symbol"] == "VUSA.L" and "listed as etf" in junk["rows"][0]["detail"]
    assert run["summary"] == kept.summary and run["notes"] == list(kept.notes)


def test_the_app_runs_the_checks_after_each_scheduled_tick(ctx, monkeypatch):
    called = []
    monkeypatch.setattr(activity_service, "submit_data_check",
                        lambda c, r, automatic=False: called.append(automatic))
    build_app(ctx).state.scheduler._after_tick()
    assert called == [True]

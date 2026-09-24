from dataclasses import replace

import pytest

from oracle.config import ModelProfile
from oracle.research.cutoff_questions import EVENTS, VERSION
from oracle.services import cutoff_service
from tests.fakes.llm import use_models
from tests.gui.jobs import run_job, wait_job
from tests.services.test_cutoff_probe import Knows


@pytest.fixture
def models(ctx, monkeypatch):
    ctx.config = replace(ctx.config, models={"big": ModelProfile("ollama", "big-model", claimed_cutoff="2025-07-01")})
    table = {"default": Knows("2025-08"), "big": Knows("2099-12")}
    use_models(monkeypatch, lambda profile: table[profile], cutoff_service)
    return table


async def test_nothing_probed_yet_names_the_models_a_probe_would_ask(client, models):
    body = (await client.get("/api/replay/cutoff")).json()
    assert body["stages"][0] == {"stage": "queries", "profile": "default"} and len(body["stages"]) == 5
    assert body["models"] == [{"profile": "default", "provider": "ollama", "model": body["models"][0]["model"],
                               "claimed": "2025-05-20", "cutoff": None, "probe": None}]
    assert (body["cutoff"], body["replay_from"], body["unprobed"]) == (None, None, ["default"])
    assert "probe default first" in body["note"] and body["question_set"] == VERSION


async def test_a_probe_runs_as_a_job_and_its_cutoff_scores_and_answers_are_kept(client, models):
    job = await run_job(client, "/api/jobs/cutoff-probe")
    assert (job["kind"], job["state"], job["link"], job["profile"]) == ("probe", "done", "#ledgers", None)
    [summary] = job["result"]["probes"]
    assert summary["cutoff"] == "2025-09-30" and "answers" not in summary

    body = (await client.get("/api/replay/cutoff")).json()
    assert (body["cutoff"], body["set_by"], body["replay_from"], body["note"]) == (
        "2025-09-30", "default", "2025-10-01", None)
    assert body["models"][0]["cutoff"] == "2025-09-30"
    probe = body["models"][0]["probe"]
    assert probe["last_known"] == "2025-08" and probe["probed_at"].startswith("2026-09-23")
    assert {"month": "2025-08", "right": 3, "total": 3, "known": True} in probe["months"]
    first = probe["answers"][0]
    assert first == {"event_id": EVENTS[0].id, "day": EVENTS[0].day.isoformat(), "question": EVENTS[0].question,
                     "expected": EVENTS[0].answer, "reply": EVENTS[0].answer, "right": True}


async def test_every_stage_model_is_probed_and_the_latest_cutoff_is_shown(ctx, client, models):
    ctx.config = replace(ctx.config, model_slots={"verdict": "big"})
    job = await run_job(client, "/api/jobs/cutoff-probe")
    assert [p["profile"] for p in job["result"]["probes"]] == ["default", "big"]
    body = (await client.get("/api/replay/cutoff")).json()
    assert [m["profile"] for m in body["models"]] == ["default", "big"]
    assert body["models"][1]["claimed"] == "2025-07-01"
    assert (body["cutoff"], body["set_by"]) == ("2026-10-31", "big") and "no replay day" in body["note"]


async def test_a_picked_model_is_probed_and_read_on_its_own(client, models):
    job = await run_job(client, "/api/jobs/cutoff-probe", model="big")
    assert job["profile"] == "big" and job["label"] == "Probe model cutoff on big"
    assert (await client.get("/api/replay/cutoff")).json()["unprobed"] == ["default"]
    body = (await client.get("/api/replay/cutoff", params={"model": "big"})).json()
    assert body["cutoff"] == "2026-10-31" and {s["profile"] for s in body["stages"]} == {"big"}


async def test_an_unknown_model_is_refused_before_anything_is_queued(client, models):
    response = await client.post("/api/jobs/cutoff-probe", params={"model": "nope"})
    assert response.status_code == 404 and "no model profile nope" in response.json()["error"]
    assert (await client.get("/api/replay/cutoff", params={"model": "nope"})).status_code == 404


async def test_a_failed_probe_can_be_retried_on_the_same_model(client, models):
    models["big"] = Knows(fail_on={1, 2})
    job = await run_job(client, "/api/jobs/cutoff-probe", model="big")
    assert job["state"] == "failed" and "stopped at question 1" in job["error"]
    models["big"] = Knows("2025-08")
    again = (await client.post(f"/api/activity/{job['id']}/retry")).json()
    assert again["profile"] == "big" and again["kind"] == "probe"
    assert (await wait_job(client, again))["result"]["probes"][0]["cutoff"] == "2025-09-30"

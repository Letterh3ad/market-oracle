from dataclasses import replace

import pytest

from oracle.config import ModelProfile
from oracle.research.cutoff_questions import CLAIMED_CUTOFF, EVENTS, VERSION
from oracle.services import cutoff_service
from tests.fakes.llm import use_models
from tests.gui.jobs import run_job, wait_job
from tests.services.test_cutoff_probe import Knows


@pytest.fixture
def models(ctx, monkeypatch):
    ctx.config = replace(ctx.config, models={"big": ModelProfile("ollama", "big-model")})
    table = {"default": Knows("2025-08"), "big": Knows("2099-12")}
    use_models(monkeypatch, lambda profile: table[profile], cutoff_service)
    return table


async def test_nothing_probed_yet_names_the_model_a_probe_would_ask(client, models):
    body = (await client.get("/api/replay/cutoff")).json()
    assert body == {"profile": "default", "provider": "ollama", "model": body["model"],
                    "claimed": CLAIMED_CUTOFF.isoformat(), "question_set": VERSION, "probe": None}


async def test_a_probe_runs_as_a_job_and_its_cutoff_scores_and_answers_are_kept(client, models):
    job = await run_job(client, "/api/jobs/cutoff-probe")
    assert (job["kind"], job["state"], job["link"], job["profile"]) == ("probe", "done", "#ledgers", None)
    assert job["result"]["cutoff"] == "2025-09-30" and "answers" not in job["result"]

    probe = (await client.get("/api/replay/cutoff")).json()["probe"]
    assert (probe["cutoff"], probe["last_known"], probe["replay_from"]) == ("2025-09-30", "2025-08", "2025-10-01")
    assert probe["profile"] == "default" and probe["probed_at"].startswith("2026-09-23")
    assert probe["note"] is None
    assert {"month": "2025-08", "right": 2, "total": 2, "known": True} in probe["months"]
    first = probe["answers"][0]
    assert first == {"event_id": EVENTS[0].id, "day": EVENTS[0].day.isoformat(), "question": EVENTS[0].question,
                     "expected": EVENTS[0].answer, "reply": EVENTS[0].answer, "right": True}


async def test_a_picked_model_is_probed_and_read_on_its_own(client, models):
    job = await run_job(client, "/api/jobs/cutoff-probe", model="big")
    assert job["profile"] == "big" and job["label"] == "Probe model cutoff on big"
    assert (await client.get("/api/replay/cutoff")).json()["probe"] is None
    probe = (await client.get("/api/replay/cutoff", params={"model": "big"})).json()["probe"]
    assert probe["cutoff"] == "2026-10-31" and "no replay day" in probe["note"]


async def test_an_unknown_model_is_refused_before_anything_is_queued(client, models):
    response = await client.post("/api/jobs/cutoff-probe", params={"model": "nope"})
    assert response.status_code == 404 and "no model profile nope" in response.json()["error"]
    assert (await client.get("/api/replay/cutoff", params={"model": "nope"})).status_code == 404


async def test_a_failed_probe_can_be_retried_on_the_same_model(client, models):
    models["big"] = Knows(fail_at=1)
    job = await run_job(client, "/api/jobs/cutoff-probe", model="big")
    assert job["state"] == "failed" and "stopped at question 1" in job["error"]
    models["big"] = Knows("2025-08")
    again = (await client.post(f"/api/activity/{job['id']}/retry")).json()
    assert again["profile"] == "big" and again["kind"] == "probe"
    assert (await wait_job(client, again))["result"]["cutoff"] == "2025-09-30"

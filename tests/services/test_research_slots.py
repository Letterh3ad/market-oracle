"""Model slots in research: each stage runs on its slot's profile, or all on a run's picked one."""

from dataclasses import replace

import pytest

from oracle.config import ModelProfile
from oracle.services.errors import UnknownModelProfile
from oracle.services.research_service import latest_research, refresh_research, research
from oracle.store.research import ResearchRepo
from tests.fakes.llm import ScriptedModel, step, use_models
from tests.services.test_reading import BODY, FACTS, by_id, note_calls, role_input, story
from tests.services.test_research import (  # noqa: F401
    VERDICT, ctx, gdelt, news, pages, searcher, sec, verdict_call,
)

STAGES = ("queries", "research", "notes", "roles", "verdict")


@pytest.fixture
def models(ctx, news, pages, monkeypatch):
    """`lead` on the default profile, `reader` on "big"; one article to read."""
    news.results["Apple"] = [story(1, "Reuters", "2026-09-20")]
    pages.pages["https://reuters.test/1"] = ("text/html", BODY)
    ctx.config = replace(ctx.config, models={"big": ModelProfile("ollama", "big")})
    lead = ScriptedModel(step("news_search", "Apple"), step("final", "Done [a1]."))
    lead.model, lead.roles = "lead", {"note_taker": FACTS, "verdict": VERDICT}
    reader = ScriptedModel()
    reader.model, reader.roles = "reader", {"note_taker": FACTS, "verdict": VERDICT}
    use_models(monkeypatch, {"default": lead, "big": reader}.get)
    return lead, reader


def slots(ctx, **names):
    ctx.config = replace(ctx.config, model_slots=names)


def suggest_calls(model):
    return [c for c in model.calls if "queries" in c[1].get("properties", {})]


def loop_calls(model):
    return [c for c in model.calls if "action" in c[1].get("properties", {})]


def roles_called(model):
    return {c[0][0]["content"][len("Role: "):].split(".")[0] for c in model.calls
            if c[0][0]["content"].startswith("Role: ")}


async def test_each_stage_is_answered_by_its_slots_model_and_the_run_says_which(ctx, models):
    lead, reader = models
    slots(ctx, notes="big", verdict="big")
    run = await research(ctx, "AAPL")
    assert suggest_calls(lead) and loop_calls(lead) and not suggest_calls(reader) and not loop_calls(reader)
    assert len(note_calls(reader)) == 1 and not note_calls(lead)
    assert "verdict" in roles_called(reader) and "verdict" not in roles_called(lead)
    assert {"bull", "bear", "risk"} <= roles_called(lead) and roles_called(reader) == {"note_taker", "verdict"}
    assert [(s.stage, s.profile, s.model) for s in run.stages] == [
        ("queries", "default", "lead"), ("research", "default", "lead"), ("notes", "big", "reader"),
        ("roles", "default", "lead"), ("verdict", "big", "reader")]
    assert (run.backend, run.model) == ("scripted", "lead") and run.mixed
    assert run.model_label == "scripted lead (queries, research, roles); scripted reader (notes, verdict)"
    assert by_id(run, 1).facts == ("Revenue rose 8% to $95bn",), "read back under the notes model's key"
    assert latest_research(ctx, "AAPL") == run


async def test_each_stage_sizes_its_prompts_by_its_own_models_context(ctx, models):
    lead, reader = models
    ctx.config = replace(ctx.config, models={"big": ModelProfile("ollama", "big", context=1024)})
    slots(ctx, verdict="big")
    run = await research(ctx, "AAPL")
    assert "Reuters" in role_input(lead, "news_analyst"), "the roles keep the default's room"
    assert "Reuters" not in verdict_call(reader) and run.report.did_not_read


async def test_a_picked_profile_runs_every_stage_whatever_the_slots(ctx, models):
    lead, reader = models
    slots(ctx, notes="default")
    reader.replies = [step("news_search", "Apple"), step("final", "Done [a1].")]
    run = await research(ctx, "AAPL", profile="big")
    assert lead.calls == []
    assert {s.profile for s in run.stages} == {"big"} and not run.mixed
    assert run.model_label == "scripted reader"


async def test_an_unknown_picked_profile_is_refused_before_the_run(ctx, models):
    with pytest.raises(UnknownModelProfile, match="no model profile huge"):
        await research(ctx, "AAPL", profile="huge")
    assert latest_research(ctx, "AAPL") is None


async def test_a_notes_slot_change_reads_again_rather_than_reusing_another_models_notes(ctx, models):
    lead, reader = models
    first = await research(ctx, "AAPL")
    assert len(note_calls(lead)) == 1
    slots(ctx, notes="big")
    lead.replies = [step("news_search", "Apple"), step("final", "Again [a1].")]
    second = await research(ctx, "AAPL")
    assert len(note_calls(reader)) == 1 and len(note_calls(lead)) == 1
    again = ResearchRepo(ctx.conn).get(first.id)
    assert by_id(again, 1).facts == by_id(second, 1).facts == tuple(FACTS["facts"])


async def test_a_fallback_is_recorded_on_the_stage_whose_model_fell_back(ctx, models):
    lead, reader = models
    slots(ctx, notes="big", verdict="big")
    reader.falls_back_to = "claude-opus-4-8"
    run = await research(ctx, "AAPL")
    assert {s.stage: s.fell_back_to for s in run.stages} == {
        "queries": (), "research": (), "notes": ("claude-opus-4-8",), "roles": (), "verdict": ("claude-opus-4-8",)}
    assert run.fell_back_to == ("claude-opus-4-8",)
    assert run.model_label == ("scripted lead (queries, research, roles); "
                               "scripted reader (notes, verdict) (claude-opus-4-8 answered when it refused)")


async def test_a_slot_whose_model_cannot_be_built_runs_on_the_research_profile_and_says_so(ctx, models, monkeypatch):
    from oracle.services.errors import ModelUnavailable

    lead, reader = models
    monkeypatch.delenv("ORACLE_TEST_UNSET_KEY", raising=False)
    keyless = ModelProfile("claude_api", "claude-opus-5", key_env="ORACLE_TEST_UNSET_KEY")
    ctx.config = replace(ctx.config, models={"claude": keyless})
    slots(ctx, verdict="claude")
    run = await research(ctx, "AAPL")
    verdict = run.stages[-1]
    assert (verdict.stage, verdict.profile, verdict.model, verdict.fell_back_to) == ("verdict", "default", "lead", ())
    assert verdict.unavailable.startswith("claude unavailable: ") and "ORACLE_TEST_UNSET_KEY" in verdict.unavailable
    assert run.report.verdict is not None and run.fell_back_to == ()
    assert run.model_label == f"scripted lead; verdict on default ({verdict.unavailable})"
    assert "answered when it refused" not in run.model_label
    slots(ctx, research="claude")
    with pytest.raises(ModelUnavailable, match="ORACLE_TEST_UNSET_KEY"):
        await research(ctx, "AAPL")


async def test_a_tick_whose_ai_account_profile_cannot_be_built_researches_on_the_slots(ctx, models, monkeypatch):
    lead, _ = models
    monkeypatch.delenv("ORACLE_TEST_UNSET_KEY", raising=False)
    monkeypatch.setattr("oracle.services.research_service.stale_research", lambda c: ["AAPL"])
    keyless = ModelProfile("claude_api", "claude-opus-5", key_env="ORACLE_TEST_UNSET_KEY")
    ctx.config = replace(ctx.config, models={"claude": keyless})
    slots(ctx, ai_account="claude")
    done = await refresh_research(ctx)
    assert done.researched == ["AAPL"] and done.failed == []
    run = latest_research(ctx, "AAPL")
    assert {s.profile for s in run.stages} == {"default"} and loop_calls(lead)
    assert all(s.unavailable.startswith("claude unavailable: ") for s in run.stages)
    assert run.model_label.startswith("scripted lead; queries, research, notes, roles, verdict on default (claude unavailable: ")


async def test_a_ticks_research_runs_on_the_ai_account_slot_else_the_slots(ctx, monkeypatch):
    from oracle.services import research_service

    asked = []

    async def fake(c, symbol, **kwargs):
        asked.append(kwargs.get("profile"))

    monkeypatch.setattr(research_service, "research", fake)
    monkeypatch.setattr(research_service, "stale_research", lambda c: ["AAPL"])
    await refresh_research(ctx)
    ctx.config = replace(ctx.config, models={"big": ModelProfile("ollama", "big")}, model_slots={"ai_account": "big"})
    await refresh_research(ctx)
    assert asked == [None, "big"]

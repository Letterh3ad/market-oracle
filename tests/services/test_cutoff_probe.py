"""The cutoff probe: dated questions to each model a replay runs on, keyword marks, and the
latest measured cutoff across them."""

from dataclasses import replace
from datetime import date, datetime, timezone

import pytest

from oracle.config import DEFAULT_CLAIMED_CUTOFF, Config, ModelProfile
from oracle.research.cutoff import SCHEMA
from oracle.research.cutoff_questions import EVENTS
from oracle.research.llm import ModelError
from oracle.services import cutoff_service
from oracle.services.context import open_context
from oracle.services.cutoff_service import (
    Unprobed, check_replay_from, measured_cutoff, probe_cutoff, probe_replay_models, replay_cutoff, stored_probe,
)
from oracle.services.errors import InvalidQuery, ModelUnavailable, UnknownModelProfile
from tests.fakes.clock import FakeClock
from tests.fakes.llm import use_models

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)
BY_QUESTION = {e.question: e for e in EVENTS}


class Knows:
    """Answers right up to `through` (a YYYY-MM month), "unknown" after; `wrong` and `right`
    name events answered against that rule; calls numbered in `fail_on` raise ModelError."""

    name = "ollama"
    model = "fake"
    reply_tokens = 1024

    def __init__(self, through: str = "", wrong: set[str] = frozenset(), right: set[str] = frozenset(),
                 fail_on: set[int] = frozenset()) -> None:
        self.through, self.wrong, self.right, self.fail_on = through, set(wrong), set(right), set(fail_on)
        self.calls: list[tuple[list[dict], dict]] = []
        self.fell_back_to: set[str] = set()

    async def reply(self, messages: list[dict], schema: dict) -> object:
        self.calls.append((messages, schema))
        if len(self.calls) in self.fail_on:
            raise ModelError("ollama is not running")
        e = BY_QUESTION[messages[-1]["content"]]
        knows = (e.month <= self.through or e.id in self.right) and e.id not in self.wrong
        return {"answer": e.answer if knows else "unknown"}


@pytest.fixture
def ctx(tmp_path):
    with open_context(Config(db_path=tmp_path / "o.db"), None, FakeClock(NOW)) as c:
        yield c


@pytest.fixture
def models(ctx, monkeypatch):
    """The default profile answers on `models["default"]`, a second profile "big" on `models["big"]`."""
    ctx.config = replace(ctx.config, models={"big": ModelProfile("ollama", "big-model")})
    table = {"default": Knows(), "big": Knows()}
    use_models(monkeypatch, lambda profile: table[profile], cutoff_service)
    return table


def slots(ctx, **names):
    ctx.config = replace(ctx.config, model_slots=names)


def month_ids(month: str) -> list[str]:
    return [e.id for e in EVENTS if e.month == month]


async def test_a_clean_cutoff_is_the_end_of_the_month_after_the_last_one_it_knew(ctx, models):
    models["default"] = Knows("2025-08")
    probe = await probe_cutoff(ctx, "default")
    assert probe.cutoff == date(2025, 9, 30) and probe.last_known == "2025-08"
    assert (probe.profile, probe.provider, probe.model) == ("default", "ollama", ctx.config.llm.model)
    assert probe.claimed == DEFAULT_CLAIMED_CUTOFF and probe.question_set and probe.probed_at == NOW
    assert len(probe.answers) == len(EVENTS)
    first = probe.answers[0]
    assert (first.event_id, first.right) == (EVENTS[0].id, True) and first.reply == EVENTS[0].answer
    scores = {s.month: (s.right, s.total) for s in probe.months}
    assert scores["2025-08"] == (3, 3) and scores["2025-09"] == (0, 3)
    assert measured_cutoff(ctx) == date(2025, 9, 30)
    assert stored_probe(ctx, "default") == probe


async def test_each_question_goes_alone_with_the_answer_schema_and_never_its_answer(ctx, models):
    await probe_cutoff(ctx, "default")
    calls = models["default"].calls
    assert len(calls) == len(EVENTS) and all(schema == SCHEMA for _, schema in calls)
    for (messages, _), e in zip(calls, EVENTS):
        assert messages[-1]["content"] == e.question
        assert all(e.answer not in m["content"] for m in messages)


async def test_noisy_answers_a_miss_in_a_known_month_and_a_lucky_late_month(ctx, models):
    models["default"] = Knows("2025-06", wrong={month_ids("2025-03")[0], month_ids("2025-06")[0]},
                              right={*month_ids("2025-10")[:2], month_ids("2025-12")[0]})
    probe = await probe_cutoff(ctx, "default")
    known = {s.month: s.known for s in probe.months}
    assert known["2025-03"] and known["2025-06"], "2 of 3 right"
    assert known["2025-10"], "a late month with 2 right still counts"
    assert not known["2025-12"], "one lucky answer is never enough"
    assert not known["2025-07"] and not known["2025-11"]
    assert probe.cutoff == date(2025, 11, 30), "the last known month, not the first miss"


async def test_the_cutoff_is_never_before_the_profiles_claimed_one(ctx, models):
    models["default"] = Knows("2025-02")
    assert (await probe_cutoff(ctx, "default")).cutoff == DEFAULT_CLAIMED_CUTOFF
    ctx.config = replace(ctx.config, models={"big": ModelProfile("ollama", "big-model", claimed_cutoff="2025-08-15")})
    models["big"] = Knows("2025-06")
    probe = await probe_cutoff(ctx, "big")
    assert (probe.claimed, probe.cutoff) == (date(2025, 8, 15), date(2025, 8, 15))


async def test_a_claim_raised_after_the_probe_raises_the_cutoff_and_can_decide_which_model_sets_it(ctx, models):
    slots(ctx, verdict="big")
    models["default"], models["big"] = Knows("2025-08"), Knows("2025-10")
    await probe_replay_models(ctx)
    assert (replay_cutoff(ctx).cutoff, replay_cutoff(ctx).set_by) == (date(2025, 11, 30), "big")
    ctx.config = replace(ctx.config, models={**ctx.config.models,
                                             "default": ModelProfile("ollama", ctx.config.llm.model,
                                                                     claimed_cutoff="2026-01-15")})
    rc = replay_cutoff(ctx)
    assert (rc.cutoff_of("default"), rc.cutoff_of("big")) == (date(2026, 1, 15), date(2025, 11, 30))
    assert (rc.cutoff, rc.set_by, rc.replay_from) == (date(2026, 1, 15), "default", date(2026, 1, 16))
    with pytest.raises(InvalidQuery, match="2026-01-15"):
        check_replay_from(ctx, date(2025, 12, 1))


async def test_a_model_that_knows_everything_leaves_no_replay_day_and_says_so(ctx, models):
    models["default"] = Knows("2099-12")
    probe = await probe_cutoff(ctx, "default")
    assert probe.last_known == "2026-09" and probe.cutoff == date(2026, 10, 31)
    assert "no replay day" in replay_cutoff(ctx).note
    with pytest.raises(InvalidQuery, match="knew events up to 2026-09.*no replay day"):
        check_replay_from(ctx, date(2026, 8, 3))


async def test_a_replay_day_must_be_strictly_after_the_cutoff(ctx, models):
    models["default"] = Knows("2025-08")
    await probe_cutoff(ctx, "default")
    rc = replay_cutoff(ctx)
    assert (rc.cutoff, rc.replay_from, rc.note) == (date(2025, 9, 30), date(2025, 10, 1), None)
    with pytest.raises(InvalidQuery, match="2025-09-30"):
        check_replay_from(ctx, date(2025, 9, 30))
    assert check_replay_from(ctx, rc.replay_from) == date(2025, 9, 30)


async def test_an_unprobed_model_is_named_and_every_replay_day_refused_until_it_is_probed(ctx, models):
    cutoff = measured_cutoff(ctx)
    assert isinstance(cutoff, Unprobed) and not cutoff and cutoff.profiles == ("default",)
    assert str(cutoff) == "unprobed: default"
    with pytest.raises(InvalidQuery, match="probe default first"):
        check_replay_from(ctx, date(2026, 1, 5))


async def test_every_stage_a_replay_runs_on_counts_and_the_latest_cutoff_wins(ctx, models):
    slots(ctx, verdict="big")
    models["default"], models["big"] = Knows("2025-08"), Knows("2025-11")
    await probe_cutoff(ctx, "default")
    assert measured_cutoff(ctx) == Unprobed(("big",)), "the verdict stage's model was never probed"
    with pytest.raises(InvalidQuery, match="probe big first"):
        check_replay_from(ctx, date(2026, 3, 2))
    await probe_cutoff(ctx, "big")
    rc = replay_cutoff(ctx)
    assert rc.stages["verdict"] == "big" and rc.stages["research"] == "default"
    assert (rc.cutoff, rc.set_by) == (date(2025, 12, 31), "big")
    assert measured_cutoff(ctx) == date(2025, 12, 31)
    assert measured_cutoff(ctx, "default") == date(2025, 9, 30), "a picked profile runs every stage"


async def test_probing_with_no_pick_probes_each_unprobed_stage_model_in_one_go(ctx, models):
    slots(ctx, notes="big")
    models["default"], models["big"] = Knows("2025-08"), Knows("2025-11")
    seen: list[str] = []
    probes = await probe_replay_models(ctx, on_progress=seen.append)
    assert [p.profile for p in probes] == ["default", "big"]
    assert seen[0] == f"default: question 1 of {len(EVENTS)} ({EVENTS[0].month})"
    assert seen[-1] == f"big: question {len(EVENTS)} of {len(EVENTS)} ({EVENTS[-1].month})"
    models["big"] = Knows("2025-12")
    await probe_cutoff(ctx, "big")
    assert [p.profile for p in await probe_replay_models(ctx)] == ["default", "big"], "all probed: all again"


async def test_probing_a_picked_model_probes_it_alone(ctx, models):
    slots(ctx, verdict="big")
    probes = await probe_replay_models(ctx, "big")
    assert [p.profile for p in probes] == ["big"] and not models["default"].calls


async def test_a_second_probe_of_the_same_model_replaces_the_first(ctx, models):
    models["default"] = Knows("2025-08")
    await probe_cutoff(ctx, "default")
    models["default"] = Knows("2025-10")
    await probe_cutoff(ctx, "default")
    probe = stored_probe(ctx, "default")
    assert probe.cutoff == date(2025, 11, 30) and len(probe.answers) == len(EVENTS)


async def test_only_events_already_past_are_asked(ctx, models):
    ctx.clock = FakeClock(datetime(2025, 9, 10, 12, 0, tzinfo=timezone.utc))
    probe = await probe_cutoff(ctx, "default")
    assert {a.event_id for a in probe.answers} == {e.id for e in EVENTS if e.day <= date(2025, 9, 10)}


async def test_a_question_the_model_fails_once_is_asked_again(ctx, models):
    models["default"] = Knows("2025-08", fail_on={5})
    probe = await probe_cutoff(ctx, "default")
    assert len(probe.answers) == len(EVENTS) and probe.answers[4].right


async def test_a_model_that_fails_a_question_twice_fails_the_probe_and_keeps_nothing(ctx, models):
    models["default"] = Knows("2025-08", fail_on={5, 6})
    with pytest.raises(ModelUnavailable, match="question 5 of .*ollama is not running"):
        await probe_cutoff(ctx, "default")
    assert stored_probe(ctx, "default") is None


async def test_a_reply_outside_the_schema_is_kept_as_text_and_marked(ctx, models):
    class Plain(Knows):
        async def reply(self, messages, schema):
            return f"<think>hmm</think>I think {(await super().reply(messages, schema))['answer']}"

    models["default"] = Plain("2025-02")
    probe = await probe_cutoff(ctx, "default")
    assert probe.answers[0].reply == f"I think {EVENTS[0].answer}" and probe.answers[0].right


async def test_an_unknown_profile_is_refused(ctx, models):
    with pytest.raises(UnknownModelProfile):
        await probe_cutoff(ctx, "nope")
    with pytest.raises(UnknownModelProfile):
        measured_cutoff(ctx, "nope")
    with pytest.raises(UnknownModelProfile):
        await probe_replay_models(ctx, "nope")

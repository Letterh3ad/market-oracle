"""The cutoff probe: dated questions to the model, keyword marks, the measured cutoff kept per model."""

from dataclasses import replace
from datetime import date, datetime, timezone

import pytest

from oracle.config import Config, ModelProfile
from oracle.research.cutoff import SCHEMA
from oracle.research.cutoff_questions import CLAIMED_CUTOFF, EVENTS
from oracle.research.llm import ModelError
from oracle.services import cutoff_service
from oracle.services.context import open_context
from oracle.services.cutoff_service import (
    check_replay_from, measured_cutoff, probe_cutoff, stored_probe,
)
from oracle.services.errors import InvalidQuery, ModelUnavailable, UnknownModelProfile
from tests.fakes.clock import FakeClock
from tests.fakes.llm import use_models

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)
BY_QUESTION = {e.question: e for e in EVENTS}


class Knows:
    """Answers right up to `through` (a YYYY-MM month), "unknown" after; `wrong` and `right`
    name events answered against that rule."""

    name = "ollama"
    model = "fake"
    reply_tokens = 1024

    def __init__(self, through: str = "", wrong: set[str] = frozenset(), right: set[str] = frozenset(),
                 fail_at: int | None = None) -> None:
        self.through, self.wrong, self.right, self.fail_at = through, set(wrong), set(right), fail_at
        self.calls: list[tuple[list[dict], dict]] = []
        self.fell_back_to: set[str] = set()

    async def reply(self, messages: list[dict], schema: dict) -> object:
        self.calls.append((messages, schema))
        if self.fail_at is not None and len(self.calls) == self.fail_at:
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


def month_ids(month: str) -> list[str]:
    return [e.id for e in EVENTS if e.month == month]


async def test_a_clean_cutoff_is_the_end_of_the_month_after_the_last_one_it_knew(ctx, models):
    models["default"] = Knows("2025-08")
    probe = await probe_cutoff(ctx)
    assert probe.cutoff == date(2025, 9, 30) and probe.last_known == "2025-08"
    assert (probe.profile, probe.provider, probe.model) == ("default", "ollama", ctx.config.llm.model)
    assert probe.claimed == CLAIMED_CUTOFF and probe.question_set and probe.probed_at == NOW
    assert len(probe.answers) == len(EVENTS)
    first = probe.answers[0]
    assert (first.event_id, first.right) == (EVENTS[0].id, True) and first.reply == EVENTS[0].answer
    scores = {s.month: (s.right, s.total) for s in probe.months}
    assert scores["2025-08"] == (2, 2) and scores["2025-09"] == (0, 3)
    assert measured_cutoff(ctx) == date(2025, 9, 30)
    assert stored_probe(ctx) == probe


async def test_each_question_goes_alone_with_the_answer_schema_and_never_its_answer(ctx, models):
    await probe_cutoff(ctx)
    calls = models["default"].calls
    assert len(calls) == len(EVENTS) and all(schema == SCHEMA for _, schema in calls)
    for (messages, _), e in zip(calls, EVENTS):
        assert messages[-1]["content"] == e.question
        assert all(e.answer not in m["content"] for m in messages)


async def test_noisy_answers_a_miss_in_a_known_month_and_a_lucky_late_month(ctx, models):
    models["default"] = Knows("2025-06", wrong={month_ids("2025-03")[0], month_ids("2025-06")[0]},
                              right={*month_ids("2025-10")[:2], month_ids("2025-12")[0]})
    probe = await probe_cutoff(ctx)
    known = {s.month: s.known for s in probe.months}
    assert known["2025-03"] and known["2025-06"], "2 of 3 is at least half"
    assert known["2025-10"] and known["2025-12"], "2 of 3 and 1 of 2: a lucky month still counts"
    assert not known["2025-07"] and not known["2025-11"]
    assert probe.cutoff == date(2026, 1, 31), "the last known month, not the first miss"


async def test_a_model_that_knows_nothing_after_the_claim_keeps_the_claimed_cutoff(ctx, models):
    models["default"] = Knows("2025-02")
    assert (await probe_cutoff(ctx)).cutoff == CLAIMED_CUTOFF


async def test_a_model_that_knows_everything_leaves_no_replay_day_and_says_so(ctx, models):
    models["default"] = Knows("2099-12")
    probe = await probe_cutoff(ctx)
    assert probe.last_known == "2026-09" and probe.cutoff == date(2026, 10, 31)
    with pytest.raises(InvalidQuery, match="knew events up to 2026-09.*no replay day"):
        check_replay_from(ctx, date(2026, 8, 3))


async def test_a_replay_day_on_or_before_the_cutoff_is_refused_and_one_after_it_passes(ctx, models):
    assert check_replay_from(ctx, date(2025, 6, 2)) is None, "nothing probed yet: nothing to check against"
    models["default"] = Knows("2025-08")
    await probe_cutoff(ctx)
    with pytest.raises(InvalidQuery, match="2025-09-30"):
        check_replay_from(ctx, date(2025, 9, 30))
    assert check_replay_from(ctx, date(2025, 10, 1)) == date(2025, 9, 30)


async def test_the_cutoff_is_kept_per_model_and_the_research_slot_picks_which(ctx, models):
    models["big"] = Knows("2025-11")
    probe = await probe_cutoff(ctx, "big")
    assert (probe.profile, probe.provider, probe.model, probe.cutoff) == ("big", "ollama", "big-model",
                                                                         date(2025, 12, 31))
    assert measured_cutoff(ctx) is None, "the default profile's model was never probed"
    assert measured_cutoff(ctx, "big") == date(2025, 12, 31)
    ctx.config = replace(ctx.config, model_slots={"research": "big"})
    assert measured_cutoff(ctx) == date(2025, 12, 31)


async def test_a_second_probe_of_the_same_model_replaces_the_first(ctx, models):
    models["default"] = Knows("2025-08")
    await probe_cutoff(ctx)
    models["default"] = Knows("2025-10")
    await probe_cutoff(ctx)
    probe = stored_probe(ctx)
    assert probe.cutoff == date(2025, 11, 30) and len(probe.answers) == len(EVENTS)


async def test_only_events_already_past_are_asked(ctx, models):
    ctx.clock = FakeClock(datetime(2025, 9, 10, 12, 0, tzinfo=timezone.utc))
    probe = await probe_cutoff(ctx)
    assert {a.event_id for a in probe.answers} == {e.id for e in EVENTS if e.day <= date(2025, 9, 10)}


async def test_a_model_that_stops_answering_fails_the_probe_and_keeps_nothing(ctx, models):
    models["default"] = Knows("2025-08", fail_at=5)
    with pytest.raises(ModelUnavailable, match="question 5 of .*ollama is not running"):
        await probe_cutoff(ctx)
    assert stored_probe(ctx) is None


async def test_a_reply_outside_the_schema_is_kept_as_text_and_marked(ctx, models):
    class Plain(Knows):
        async def reply(self, messages, schema):
            return f"I think {(await super().reply(messages, schema))['answer']}"

    models["default"] = Plain("2025-02")
    probe = await probe_cutoff(ctx)
    assert probe.answers[0].reply == f"I think {EVENTS[0].answer}" and probe.answers[0].right


async def test_progress_names_the_question_and_its_month(ctx, models):
    seen: list[str] = []
    await probe_cutoff(ctx, on_progress=seen.append)
    assert seen[0] == f"question 1 of {len(EVENTS)} ({EVENTS[0].month})"
    assert len(seen) == len(EVENTS)


async def test_an_unknown_profile_is_refused(ctx, models):
    with pytest.raises(UnknownModelProfile):
        await probe_cutoff(ctx, "nope")
    with pytest.raises(UnknownModelProfile):
        measured_cutoff(ctx, "nope")

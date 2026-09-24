"""The cutoff probe: what the model in use actually knows, measured with dated public events,
so replays never start on a day it may already know the outcome of."""

from __future__ import annotations

import calendar
from collections.abc import Callable, Sequence
from dataclasses import dataclass, fields
from datetime import date, timedelta

from oracle.config import model_profiles
from oracle.research.cutoff import SCHEMA, Event, MonthScore, answered, cutoff_from, messages, month_scores, reply_text
from oracle.research.cutoff_questions import CLAIMED_CUTOFF, EVENTS, VERSION
from oracle.research.llm import ModelError
from oracle.services.context import AppContext
from oracle.services.errors import InvalidQuery, ModelUnavailable, UnknownModelProfile
from oracle.services.models_service import resolve_model, slot_profile
from oracle.store.cutoff import AnswerRow, CutoffRepo, ProbeRow

__all__ = ["CLAIMED_CUTOFF", "VERSION", "CutoffProbe", "check_replay_from", "measured_cutoff", "no_replay_room",
           "probe_cutoff", "probe_profile", "profile_model", "stored_probe"]

SLOT = "research"
"""Replays run research, so the research slot's model is the one whose cutoff matters."""


@dataclass(frozen=True)
class CutoffProbe(ProbeRow):
    @property
    def months(self) -> list[MonthScore]:
        return month_scores((a.day, a.right) for a in self.answers)

    @property
    def replay_from(self) -> date:
        """The first day a replay may use."""
        return self.cutoff + timedelta(days=1)

    @property
    def last_known(self) -> str | None:
        """The last month answered at least half right."""
        return max((s.month for s in self.months if s.known), default=None)


def probe_profile(ctx: AppContext, profile: str | None) -> str:
    """The named profile, or the research slot's; an unknown one is refused."""
    name = profile or slot_profile(ctx.config, SLOT)
    if name not in model_profiles(ctx.config):
        raise UnknownModelProfile(f"no model profile {name}")
    return name


async def probe_cutoff(ctx: AppContext, profile: str | None = None, on_progress: Callable[[str], None] | None = None,
                       events: Sequence[Event] = EVENTS) -> CutoffProbe:
    """Ask every event up to today, one at a time, mark each reply by its keywords, and keep the
    result for the profile's model, replacing any earlier probe of it. A model that stops
    answering fails the probe and keeps nothing: a cutoff from half the questions would mislead."""
    name = probe_profile(ctx, profile)
    provider, model_id = profile_model(ctx, name)
    model = resolve_model(ctx, name).model
    today = ctx.clock.utcnow().date()
    asked = [e for e in events if e.day <= today]
    answers = []
    for n, e in enumerate(asked, 1):
        if on_progress:
            on_progress(f"question {n} of {len(asked)} ({e.month})")
        try:
            reply = reply_text(await model.reply(messages(e), SCHEMA))
        except ModelError as exc:
            raise ModelUnavailable(f"the probe stopped at question {n} of {len(asked)}: {exc}") from exc
        answers.append(AnswerRow(e.id, e.day, e.question, e.answer, reply, answered(reply, e.expect)))
    scores = month_scores((a.day, a.right) for a in answers)
    probe = CutoffProbe(provider, model_id, name, VERSION, CLAIMED_CUTOFF, cutoff_from(scores, CLAIMED_CUTOFF),
                        ctx.clock.utcnow(), tuple(answers))
    CutoffRepo(ctx.conn).replace(probe)
    return probe


def profile_model(ctx: AppContext, profile: str | None = None) -> tuple[str, str]:
    """The provider and model id a probe of the profile (else the research slot) is kept under."""
    spec = model_profiles(ctx.config)[probe_profile(ctx, profile)]
    return spec.provider, spec.model


def stored_probe(ctx: AppContext, profile: str | None = None) -> CutoffProbe | None:
    """The latest probe of the model the profile (else the research slot) names, if any."""
    row = CutoffRepo(ctx.conn).get(*profile_model(ctx, profile))
    return None if row is None else CutoffProbe(**{f.name: getattr(row, f.name) for f in fields(row)})


def measured_cutoff(ctx: AppContext, profile: str | None = None) -> date | None:
    """The last day the model may know about, measured; None until it has been probed."""
    probe = stored_probe(ctx, profile)
    return probe.cutoff if probe else None


def no_replay_room(ctx: AppContext, probe: CutoffProbe) -> str | None:
    """Why no replay can start after the probe's cutoff, when none can: every day after it is
    within a month of today, too recent to score."""
    if probe.cutoff < _month_back(ctx.clock.utcnow().date()):
        return None
    return (f"{probe.provider} {probe.model} knew events up to {probe.last_known}, so its cutoff is {probe.cutoff}: "
            "no replay day after that has a month of prices to score it yet. Replays need a model that knows "
            "less of the recent past; probe another profile.")


def check_replay_from(ctx: AppContext, day: date, profile: str | None = None) -> date | None:
    """Refuse a replay day on or before the model's measured cutoff, saying why; the cutoff, or
    None when nothing has been probed (there is nothing to check against yet)."""
    probe = stored_probe(ctx, profile)
    if probe is None:
        return None
    if note := no_replay_room(ctx, probe):
        raise InvalidQuery(note)
    if day <= probe.cutoff:
        raise InvalidQuery(f"{day} is on or before {probe.provider} {probe.model}'s measured cutoff {probe.cutoff} "
                           f"(it knew events up to {probe.last_known or 'its claimed cutoff'}): a replay then could "
                           f"see ahead. Pick a day after {probe.cutoff}.")
    return probe.cutoff


def _month_back(day: date) -> date:
    """The same day a month earlier, or that month's last day when it is shorter."""
    year, month = (day.year - 1, 12) if day.month == 1 else (day.year, day.month - 1)
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))

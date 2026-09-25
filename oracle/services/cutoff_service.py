"""The cutoff probe: what each model a replay runs on actually knows, measured with dated public
events, so replays never start on a day any of them may already know the outcome of."""

from __future__ import annotations

import calendar
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, fields
from datetime import date, timedelta

from oracle.config import claimed_cutoff, model_profiles
from oracle.research.cutoff import SCHEMA, Event, MonthScore, answered, cutoff_from, messages, month_scores, reply_text
from oracle.research.cutoff_questions import EVENTS, VERSION
from oracle.research.llm import ModelError
from oracle.services.context import AppContext
from oracle.services.errors import InvalidQuery, ModelUnavailable, UnknownModelProfile
from oracle.services.models_service import RESEARCH_STAGES, resolve_model, slot_profile
from oracle.store.cutoff import AnswerRow, CutoffRepo, ProbeRow

__all__ = ["VERSION", "CutoffProbe", "ModelFacts", "Pin", "ReplayCutoff", "Unprobed", "cannot_run",
           "check_replay_from", "measured_cutoff", "month_back", "pin_stages", "pinned_cutoff", "pins", "probe_cutoff",
           "probe_replay_models", "profile_model", "replay_cutoff", "replay_profiles", "stored_probe"]

TRIES = 2
"""Asks per question: a cold local model can fail its first reply while it loads."""


@dataclass(frozen=True)
class CutoffProbe(ProbeRow):
    @property
    def months(self) -> list[MonthScore]:
        return month_scores((a.day, a.right) for a in self.answers)

    @property
    def last_known(self) -> str | None:
        """The last month answered well enough to count as known."""
        return max((s.month for s in self.months if s.known), default=None)


@dataclass(frozen=True)
class Unprobed:
    """What `measured_cutoff` gives while some model a replay runs on has not been probed: falsy,
    so no cutoff is taken from it, and naming the profiles to probe."""

    profiles: tuple[str, ...]

    def __bool__(self) -> bool:
        return False

    def __str__(self) -> str:
        return f"unprobed: {', '.join(self.profiles)}"


@dataclass(frozen=True)
class ModelFacts:
    """What a profile names today: its probe is kept under provider and model."""

    provider: str
    model: str
    claimed: date


@dataclass(frozen=True)
class ReplayCutoff:
    """The models a replay runs on, stage by stage, and what their probes allow."""

    stages: dict[str, str]
    """Each research stage and the profile it runs on."""
    probes: dict[str, CutoffProbe | None]
    """Each distinct profile, in stage order, and its stored probe."""
    facts: dict[str, ModelFacts]
    """The same profiles and what they name."""
    latest_day: date
    """The last day a replay may use and still have a month of prices after it."""

    @property
    def unprobed(self) -> tuple[str, ...]:
        return tuple(name for name, p in self.probes.items() if p is None)

    def cutoff_of(self, name: str) -> date | None:
        """The profile's measured cutoff, raised to its claimed cutoff as it stands now: a claim
        raised in Settings after the probe still counts."""
        probe = self.probes[name]
        return max(probe.cutoff, self.facts[name].claimed) if probe else None

    @property
    def set_by(self) -> str | None:
        """The profile whose cutoff is latest, once every one is probed."""
        if self.unprobed:
            return None
        return max(self.probes, key=self.cutoff_of)

    @property
    def cutoff(self) -> date | None:
        return self.cutoff_of(self.set_by) if self.set_by else None

    @property
    def replay_from(self) -> date | None:
        """The first day a replay may use: strictly after the cutoff."""
        return self.cutoff + timedelta(days=1) if self.cutoff else None

    @property
    def note(self) -> str | None:
        """Why no replay can start now, if none can."""
        if missing := self.unprobed:
            return (f"replays run on {', '.join(self.probes)}: probe {', '.join(missing)} first (Probe cutoff on "
                    "the Ledgers tab, or oracle replay probe), since a model never probed may know what came next.")
        if self.cutoff >= self.latest_day:
            p = self.probes[self.set_by]
            return (f"{p.provider} {p.model} ({p.profile}) knew events up to {p.last_known} and claims "
                    f"{self.facts[self.set_by].claimed}, so the cutoff is {self.cutoff}: no replay day after that "
                    "has a month of prices to score it yet. Replays need "
                    "models that know less of the recent past; point the slots at another profile and probe it.")
        return None


def probe_profile(ctx: AppContext, profile: str) -> str:
    if profile not in model_profiles(ctx.config):
        raise UnknownModelProfile(f"no model profile {profile}")
    return profile


def profile_model(ctx: AppContext, profile: str) -> tuple[str, str]:
    """The provider and model id a probe of the profile is kept under."""
    spec = model_profiles(ctx.config)[probe_profile(ctx, profile)]
    return spec.provider, spec.model


def replay_profiles(ctx: AppContext, profile: str | None = None) -> dict[str, str]:
    """Each research stage and the profile a replay runs it on: the slots', or `profile` for all."""
    stages = {stage: profile or slot_profile(ctx.config, stage) for stage in RESEARCH_STAGES}
    for name in set(stages.values()):
        probe_profile(ctx, name)
    return stages


async def probe_cutoff(ctx: AppContext, profile: str, on_progress: Callable[[str], None] | None = None,
                       events: Sequence[Event] = EVENTS) -> CutoffProbe:
    """Ask the profile's model every event up to today, one at a time (twice if a reply fails),
    mark each reply by its keywords, and keep the result for that model, replacing any earlier
    probe of it. A model that stops answering fails the probe and keeps nothing: a cutoff from
    half the questions would mislead."""
    provider, model_id = profile_model(ctx, profile)
    claimed = claimed_cutoff(model_profiles(ctx.config)[profile])
    model = resolve_model(ctx, profile).model
    today = ctx.clock.utcnow().date()
    asked = [e for e in events if e.day <= today]
    answers = []
    for n, e in enumerate(asked, 1):
        if on_progress:
            on_progress(f"question {n} of {len(asked)} ({e.month})")
        reply = reply_text(await _ask(model, e, f"question {n} of {len(asked)}"))
        answers.append(AnswerRow(e.id, e.day, e.question, e.answer, reply, answered(reply, e.expect)))
    scores = month_scores((a.day, a.right) for a in answers)
    probe = CutoffProbe(provider, model_id, profile, VERSION, claimed, cutoff_from(scores, claimed),
                        ctx.clock.utcnow(), tuple(answers))
    CutoffRepo(ctx.conn).replace(probe)
    return probe


async def _ask(model, e: Event, where: str) -> object:
    for attempt in range(1, TRIES + 1):
        try:
            return await model.reply(messages(e), SCHEMA)
        except ModelError as exc:
            if attempt == TRIES:
                raise ModelUnavailable(f"the probe stopped at {where}: {exc}") from exc


async def probe_replay_models(ctx: AppContext, profile: str | None = None,
                              on_progress: Callable[[str], None] | None = None) -> list[CutoffProbe]:
    """Probe `profile`, or with none each model a replay runs on that has no probe yet (all of
    them again when every one has), one after the other."""
    rc = replay_cutoff(ctx, profile)
    names = [profile] if profile else list(rc.unprobed or rc.probes)
    probes = []
    for name in names:
        say = (lambda line, name=name: on_progress(f"{name}: {line}")) if on_progress else None
        probes.append(await probe_cutoff(ctx, name, on_progress=say))
    return probes


def stored_probe(ctx: AppContext, profile: str) -> CutoffProbe | None:
    """The latest probe of the model the profile names, if any."""
    row = CutoffRepo(ctx.conn).get(*profile_model(ctx, profile))
    return None if row is None else CutoffProbe(**{f.name: getattr(row, f.name) for f in fields(row)})


def replay_cutoff(ctx: AppContext, profile: str | None = None) -> ReplayCutoff:
    """What the probes allow a replay on the slots' models, or every stage on `profile`."""
    stages = replay_profiles(ctx, profile)
    names = list(dict.fromkeys(stages.values()))
    specs = model_profiles(ctx.config)
    return ReplayCutoff(stages, {name: stored_probe(ctx, name) for name in names},
                        {n: ModelFacts(specs[n].provider, specs[n].model, claimed_cutoff(specs[n])) for n in names},
                        month_back(ctx.clock.utcnow().date()))


def measured_cutoff(ctx: AppContext, profile: str | None = None) -> date | Unprobed:
    """The last day any model a replay runs on may know about: the latest of their measured
    cutoffs, or Unprobed naming those not probed yet."""
    rc = replay_cutoff(ctx, profile)
    return Unprobed(rc.unprobed) if rc.unprobed else rc.cutoff


def check_replay_from(ctx: AppContext, day: date, profile: str | None = None) -> date:
    """Refuse a replay day unless every model the replay runs on is probed and the day is
    strictly after the latest cutoff, saying why; the cutoff when it passes."""
    rc = replay_cutoff(ctx, profile)
    if note := rc.note:
        raise InvalidQuery(note)
    if day <= rc.cutoff:
        p = rc.probes[rc.set_by]
        raise InvalidQuery(f"{day} is on or before the measured cutoff {rc.cutoff} ({p.provider} {p.model}, "
                           f"{p.profile}, knew events up to {p.last_known or 'its claimed cutoff'}): a replay then "
                           f"could see ahead. Pick {rc.replay_from} or later.")
    return rc.cutoff


def month_back(day: date) -> date:
    """The same day a month earlier, or that month's last day when it is shorter."""
    year, month = (day.year - 1, 12) if day.month == 1 else (day.year, day.month - 1)
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


@dataclass(frozen=True)
class Pin:
    """The profile a study ran a stage on, and the model it named then (None in studies from
    before models were pinned, which compare by profile name)."""

    profile: str
    provider: str | None = None
    model: str | None = None

    def __str__(self) -> str:
        return f"{self.profile} ({self.provider} {self.model})" if self.model else self.profile


def pin_stages(ctx: AppContext) -> str:
    """JSON for a new study: each stage's profile and the provider and model it names now."""
    rc = replay_cutoff(ctx)
    return json.dumps({stage: [name, rc.facts[name].provider, rc.facts[name].model]
                       for stage, name in rc.stages.items()})


def pins(raw: str | None) -> dict[str, Pin]:
    """A study's stages as stored: `{stage: [profile, provider, model]}`, or once `{stage: profile}`."""
    stored = json.loads(raw) if raw else {}
    return {stage: Pin(v) if isinstance(v, str) else Pin(*v) for stage, v in stored.items()}


def cannot_run(ctx: AppContext, raw: str | None) -> tuple[str | None, ReplayCutoff]:
    """Why a study with these pinned stages may not replay now (a stage's model changed, a
    model is unprobed, or the cutoff leaves no day), and the replay cutoff as it stands. A
    profile renamed but naming the same model is no change; the same name on another model is."""
    rc = replay_cutoff(ctx)
    was = pins(raw) if raw else {stage: Pin(name) for stage, name in rc.stages.items()}
    changed = []
    for stage, name in rc.stages.items():
        pin, now = was.get(stage), rc.facts[name]
        if pin is not None and pin.model:
            if (pin.provider, pin.model) != (now.provider, now.model):
                changed.append(f"{stage} {pin} to {Pin(name, now.provider, now.model)}")
        elif pin is None or pin.profile != name:
            changed.append(f"{stage} {pin} to {name}")
    if changed:
        return ("the replay models changed since the study was created: " + ", ".join(changed)
                + "; point the slots back, or create a new study"), rc
    return rc.note, rc


def pinned_cutoff(ctx: AppContext, raw: str | None) -> date | None:
    """The one cutoff of a study, for its pacer, its Activity retries and its score alike: each
    pinned model's probe, raised to the claim as it stands now of the profile the study pinned
    (the stage's slot profile when that one is gone or names another model), never to a claim on
    some other profile of the same model. The slots' cutoff when a pinned model is not probed or
    its profile is gone."""
    specs = model_profiles(ctx.config)
    cutoffs = []
    for stage, pin in pins(raw).items():
        if pin.model:
            model = (pin.provider, pin.model)
        elif pin.profile in specs:
            model = (specs[pin.profile].provider, specs[pin.profile].model)
        else:
            return replay_cutoff(ctx).cutoff
        probe = CutoffRepo(ctx.conn).get(*model)
        if probe is None:
            return replay_cutoff(ctx).cutoff
        claims = [claimed_cutoff(specs[name]) for name in (pin.profile, slot_profile(ctx.config, stage))
                  if name in specs and (specs[name].provider, specs[name].model) == model][:1]
        cutoffs.append(max([probe.cutoff, probe.claimed, *claims]))
    return max(cutoffs) if cutoffs else replay_cutoff(ctx).cutoff

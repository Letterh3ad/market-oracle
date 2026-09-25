"""The replay study: research replayed at past dates after the model's cutoff, over a fixed
spread of listings, one replay at a time in the PC's free time. It never starts by itself; the
pacer submits a replay only after Start, and only when nothing else wants the machine."""

from __future__ import annotations

import calendar
import json
import logging
import os
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from datetime import date, datetime, timedelta

from oracle.jobs import pid_alive
from oracle.services.activity_service import Job, JobRunner, submit_research
from oracle.services.context import AppContext
from oracle.services.cutoff_service import (
    ReplayCutoff, check_replay_from, month_back, pin_stages, pinned_cutoff, pins, replay_cutoff,
)
from oracle.services.cutoff_service import cannot_run as _cannot_run
from oracle.services.errors import InvalidQuery, PriceUnavailable, ServiceError, UnknownSymbol
from oracle.services.jobs_service import held_elsewhere
from oracle.services.never_list import never_company
from oracle.services.prices_service import update_listing
from oracle.services.replay_score import Interim, score_study
from oracle.services.research_service import pc_in_use
from oracle.services.schedule_service import Scheduler
from oracle.services.tick_service import KIND_LABELS
from oracle.store.bars import BarRepo
from oracle.store.health import HealthRepo
from oracle.store.job_history import NOT_RUN, JobHistoryRepo
from oracle.store.replay import DONE, FAILED, PAUSED, PENDING, RUNNING, CellRow, ReplayRepo, StudyRow
from oracle.store.research import ResearchRepo
from oracle.store.universe import UniverseRepo
from oracle.universe.listings import SIZES, Listing, is_receipt
from oracle.universe.regions import home_country, home_line

__all__ = ["Picked", "ReplayPacer", "StudyStatus", "create_study", "pause_study", "pick_listings",
           "replace_listing", "retry_failed", "start_study", "study_status", "submit_create", "submit_replace"]

log = logging.getLogger(__name__)

# The study's own spread: Europe here leaves out the UK, which has slots of its own.
STUDY_REGIONS: dict[str, tuple[str, ...]] = {
    "US": ("United States",),
    "UK": ("United Kingdom",),
    "Europe": ("Germany", "France", "Netherlands", "Switzerland", "Spain", "Italy", "Sweden", "Denmark", "Belgium",
               "Finland", "Ireland", "Norway", "Austria", "Portugal"),
    "Japan": ("Japan",),
    "Asia": ("South Korea", "Taiwan", "Hong Kong", "Singapore"),
    "Emerging": ("India", "Brazil", "Mexico", "South Africa", "Indonesia"),
}
_IT, _FIN, _HEALTH, _ENERGY = "Information Technology", "Financials", "Health Care", "Energy"
_DISC, _STAPLES, _IND, _MAT = "Consumer Discretionary", "Consumer Staples", "Industrials", "Materials"
SLOTS: tuple[tuple[str, str], ...] = (
    ("US", _IT), ("US", _FIN), ("US", _HEALTH), ("US", _DISC), ("US", _IND),
    ("UK", _FIN), ("UK", _ENERGY), ("UK", _HEALTH), ("UK", _MAT), ("UK", _STAPLES),
    ("Europe", _IT), ("Europe", _IND), ("Europe", _DISC), ("Europe", _HEALTH),
    ("Japan", _DISC), ("Japan", _IT), ("Japan", _IND),
    ("Asia", _IT), ("Asia", _FIN),
    ("Emerging", _ENERGY),
)
"""One listing per slot and half: about 20 each, every region and sector the study needs."""
LARGE, MID_SMALL = SIZES
MIX = "mix"
HALVES: dict[str, tuple[tuple[str, ...], ...]] = {
    LARGE: (("Mega Cap", "Large Cap", "Mid Cap"),),
    # A band at a time, so a slot's Small Caps are tried only when none of its Mid Caps fits.
    MID_SMALL: (("Mid Cap",), ("Small Cap",)),
}
"""Each half of the pick and the bands it tries in turn; Micro and Nano Caps never."""
PICKS: dict[str, tuple[str, ...]] = {LARGE: (LARGE,), MID_SMALL: (MID_SMALL,), MIX: (LARGE, MID_SMALL)}
"""What `bands` asks for: the halves picked."""
TRIES_PER_SLOT = 4
"""Candidates a slot fetches prices for before it is left out."""
RECENT = timedelta(days=10)
"""A listing's last stored price must be this recent: it still trades."""
OTHER = "Other"

DEFAULT_REPLAY = timedelta(minutes=5)
"""The expected length of a replay before any has finished."""
MARGIN = timedelta(minutes=10)
PREEMPT_WITHIN = timedelta(minutes=1)
"""A running replay is stopped when a scheduled run is this close."""
RECENT_REPLAYS = 10
ALLOWED = 2
"""Attempts a cell gets: its first and one automatic retry."""
OUTAGE_AFTER = 3
"""Failures in a row with the same error that mean an outage (the model or the network), not bad cells."""
ANY_OUTAGE_AFTER = 5
"""Failures in a row whatever their errors that mean an outage: an error's text can vary per cell."""
MAX_REFUNDS = 2
"""Outages a cell's failure may be put down to; after that it counts, so a bad cell still ends failed."""
BACKOFF = timedelta(minutes=45)
MAX_BACKOFFS = 4
"""Back-offs of BACKOFF before they lengthen to LONG_BACKOFF; an outage never pauses the study."""
LONG_BACKOFF = timedelta(hours=2)
HARD_CAP = timedelta(minutes=45)
"""The least a replay may run before it is stopped as hung: the cap is 3 times the longest recent one."""
PREEMPTED_BY = ("research", "tick", "ask", "discover")
"""Kinds of other work a running replay gives way to: they need the model."""
TOP_FAILURES = 3
POLL_S = 10.0


@dataclass(frozen=True)
class Picked:
    symbol: str
    name: str
    region: str
    sector: str
    market_cap: str | None
    """The company's band the pick ranked it by (its primary listing's)."""
    half: str | None = None
    """The half of the study it was picked or named for; None in studies from before halves."""


@dataclass(frozen=True)
class RunningCell:
    id: int
    symbol: str
    as_of: date
    job_id: int | None
    age_s: float


@dataclass(frozen=True)
class FailedCell:
    id: int
    symbol: str
    as_of: date
    error: str | None
    attempts: int


@dataclass(frozen=True)
class StudyStatus:
    id: int
    state: str
    """paused or running."""
    paused_reason: str | None
    created_at: datetime
    first_day: date
    last_day: date
    listings: tuple[Picked, ...]
    total: int
    done: int
    pending: int
    """Waiting their turn, the automatic retries among them."""
    retrying: int
    """Pending cells that failed before."""
    running: tuple[RunningCell, ...]
    failed: tuple[FailedCell, ...]
    """Failed for good, until Retry."""
    top_failures: tuple[tuple[str, int], ...]
    mean_replay_s: float | None
    cutoff: date | None
    waiting: str | None = None
    """Why the pacer held the next replay back at its last poll."""
    polled_at: datetime | None = None
    """The pacer's last poll: an old one means no GUI server is pacing the study."""
    untouched: bool = False
    """No replay submitted yet, so a listing can still be replaced."""
    left_out: tuple[str, ...] = ()
    """Slots the pick could not fill; told only by the create that left them out."""
    score: Interim | None = None
    stages: dict[str, str] = field(default_factory=dict)
    """Each research stage and the profile the study runs it on, fixed at creation."""
    stage_models: dict[str, str] = field(default_factory=dict)
    """Each research stage's provider and model, fixed at creation; empty in older studies."""

    @property
    def finished(self) -> bool:
        """Nothing left to run; failed cells may remain."""
        return self.pending == 0 and not self.running

    @property
    def complete(self) -> bool:
        """Every cell done."""
        return self.done == self.total

    def to_json(self) -> dict:
        """Plain JSON types, the same for the CLI's --json and the GUI's endpoint."""
        return json.loads(json.dumps({**asdict(self), "finished": self.finished, "complete": self.complete},
                                     default=_iso))


def _iso(value: date | datetime) -> str:
    return value.isoformat()


def _when(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _months(first: date, last: date) -> list[tuple[date, date]]:
    """Each month from `first`'s to `last`'s, clipped to them."""
    out, y, m = [], first.year, first.month
    while date(y, m, 1) <= last:
        out.append((max(first, date(y, m, 1)), min(last, date(y, m, calendar.monthrange(y, m)[1]))))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def replay_cells(ctx: AppContext, symbols: list[str], first: date, last: date) -> list[tuple[str, date]]:
    """Each listing's first trading day of each month (its first stored bar there, so a
    holiday is never a replay day), oldest month first so early results have their longest
    outcomes."""
    bars = BarRepo(ctx.conn)
    return [(s, d) for start, end in _months(first, last) for s in symbols
            if (d := bars.first_between(s, start, end))]


def _region_of(listing: Listing) -> str | None:
    """The region whose main board and currency the line trades on."""
    country = home_country(listing)
    return next((r for r, countries in STUDY_REGIONS.items() if country in countries), None)


async def _price_problem(ctx: AppContext, symbol: str, first: date) -> str | None:
    """Why the listing's prices cannot score a study from `first` (fetching them when the store
    falls short), or None."""
    bars = BarRepo(ctx.conn)
    recent = ctx.clock.utcnow().date() - RECENT
    span = bars.span([symbol])
    if not span or span[0] > first or span[1] < recent:
        try:
            await update_listing(ctx, symbol)
        except PriceUnavailable as exc:
            if not exc.answered:
                # No source answered, which says nothing about the listing: picking on
                # without it would quietly change the spread.
                raise InvalidQuery(f"the price sources did not answer ({exc}): try again once they do") from exc
            if exc.no_data:
                return str(exc)
            return f"no source that answered has data for {symbol} ({', '.join(exc.down)} did not answer)"
        except UnknownSymbol as exc:
            return str(exc)
        span = bars.span([symbol])
    if not span:
        return f"no prices for {symbol}"
    if span[0] > first:
        return f"{symbol}'s prices start {span[0].isoformat()}, after {first.isoformat()}"
    if span[1] < recent:
        return f"{symbol}'s last price is {span[1].isoformat()}: it no longer trades"
    return None


async def pick_listings(ctx: AppContext, first: date, say: Callable[[str], None] = lambda _: None,
                        bands: str = LARGE, large: list[Picked] | None = None) -> tuple[list[Picked], list[str]]:
    """One listing per slot for each half `bands` asks for (`large` stands in for the large
    half when given): in the half's first band that has one, the biggest band, then the most
    widely listed company, on its region's main board in its currency, that is not on the never
    list, a receipt or dead, and whose prices run from `first` to now (fetched here). Also the
    slots nothing filled."""
    _check_bands(bands, None)
    universe = UniverseRepo(ctx.conn)
    dead = {s.upper() for s in HealthRepo(ctx.conn).dead_symbols()}
    picked: list[Picked] = []
    left_out: list[str] = []
    for half in PICKS[bands]:
        if half == LARGE and large is not None:
            picked += large
            continue
        tag = "" if half == LARGE else " (mid/small)"
        for region, sector in SLOTS:
            for group in HALVES[half]:
                if chosen := await _pick_slot(ctx, first, universe, dead, region, sector, group, picked, say):
                    picked.append(replace(chosen, half=half))
                    break
            else:
                left_out.append(f"{region} {sector}{tag}")
                say(f"{region} {sector}{tag}: nothing fits, left out")
    return picked, left_out


async def _pick_slot(ctx: AppContext, first: date, universe: UniverseRepo, dead: set[str], region: str,
                     sector: str, group: tuple[str, ...], picked: list[Picked],
                     say: Callable[[str], None]) -> Picked | None:
    tries = 0
    for company in universe.largest_in(STUDY_REGIONS[region], sector, group):
        if tries == TRIES_PER_SLOT:
            return None
        line = home_line(universe.company_listings(company.company_key), STUDY_REGIONS[region], dead)
        if (line is None or any(p.symbol == line.symbol for p in picked) or is_receipt(line.name)
                or never_company(ctx, line.symbol, user=True)):
            continue
        tries += 1
        say(f"{region} {sector}: checking {line.symbol}")
        if problem := await _price_problem(ctx, line.symbol, first):
            say(f"  passed over: {problem}")
            continue
        return Picked(line.symbol, company.name, region, sector, company.market_cap)
    return None


async def check_symbol(ctx: AppContext, symbol: str, first: date) -> Picked:
    """A listing the user named, held to the same rules as a picked one except its slot; it
    stands in for the large half, and without a band of its own takes its company's."""
    universe = UniverseRepo(ctx.conn)
    resolved = universe.resolve(symbol)
    profile = universe.profile(symbol)
    if resolved is None or profile is None:
        raise InvalidQuery(f"{symbol} is not in the universe")
    if profile.type != "equity" or is_receipt(resolved.name):
        raise InvalidQuery(f"{resolved.symbol} is not a company's own shares")
    if company := never_company(ctx, resolved.symbol, user=True):
        raise InvalidQuery(f"{company} is on your never list")
    if problem := await _price_problem(ctx, resolved.symbol, first):
        raise InvalidQuery(problem)
    line = next((x for x in universe.company_listings(resolved.company_key) if x.symbol == resolved.symbol), None)
    region = (line and _region_of(line)) or OTHER
    return Picked(resolved.symbol, resolved.name, region, profile.sector or "",
                  profile.market_cap or resolved.market_cap, LARGE)


def _window(ctx: AppContext, first: date | None) -> tuple[date, date]:
    """The first and last replay day a new study may use, refusing what cannot be one."""
    if ReplayRepo(ctx.conn).any_running():
        raise InvalidQuery("a replay is running: wait for it, or pause the study and let it finish, first")
    rc = replay_cutoff(ctx)
    if rc.note:  # a stage's model is unprobed, or knows too recent a past for any replay
        raise InvalidQuery(rc.note)
    first = first or rc.replay_from
    check_replay_from(ctx, first)
    last = month_back(ctx.clock.utcnow().date())
    if first > last:
        raise InvalidQuery(f"{first.isoformat()} leaves no replay a month of outcome: pick an earlier day")
    return first, last


async def create_study(ctx: AppContext, first: date | None = None, symbols: list[str] | None = None,
                       say: Callable[[str], None] = lambda _: None, bands: str = MIX) -> StudyStatus:
    """A new study, paused: its listings (the halves `bands` asks for, `symbols` standing in
    for the large one) checked and saved, one cell per listing and month."""
    first, last = _window(ctx, first)
    _check_bands(bands, symbols)
    large = [await check_symbol(ctx, s, first) for s in dict.fromkeys(symbols)] if symbols else None
    picked, left_out = await pick_listings(ctx, first, say, bands, large)
    if not picked:
        raise InvalidQuery("no listing in the universe fits the spread: refresh the universe first")
    _window(ctx, first)  # a replay may have started while prices were fetched
    cells = replay_cells(ctx, [p.symbol for p in picked], first, last)
    ReplayRepo(ctx.conn).create(ctx.clock.utcnow(), min(d for _, d in cells), max(d for _, d in cells),
                                _listings_json(picked), cells, ALLOWED, pin_stages(ctx))
    return replace(_require_status(ctx), left_out=tuple(left_out))


async def replace_listing(ctx: AppContext, old: str, new: str) -> StudyStatus:
    """Swap one listing of a study that has not run a replay yet."""
    study = _untouched(ctx)
    listings = [Picked(**p) for p in json.loads(study.listings)]
    at = next((i for i, p in enumerate(listings) if p.symbol.casefold() == old.casefold()), None)
    if at is None:
        raise InvalidQuery(f"{old} is not one of the study's listings")
    chosen = await check_symbol(ctx, new, study.first_day)
    if any(p.symbol == chosen.symbol for p in listings):
        raise InvalidQuery(f"{chosen.symbol} is already in the study")
    study = _untouched(ctx)
    listings[at] = Picked(chosen.symbol, chosen.name, listings[at].region if chosen.region == OTHER else chosen.region,
                          chosen.sector or listings[at].sector, chosen.market_cap, listings[at].half)
    rc = replay_cutoff(ctx)
    if rc.note:
        raise InvalidQuery(rc.note)
    last = month_back(ctx.clock.utcnow().date())
    cells = replay_cells(ctx, [p.symbol for p in listings], max(study.first_day, rc.replay_from), last)
    ReplayRepo(ctx.conn).replace_cells(study.id, min(d for _, d in cells), max(d for _, d in cells),
                                       _listings_json(listings), cells, ALLOWED)
    return _require_status(ctx)


def _untouched(ctx: AppContext) -> StudyRow:
    study = _require(ctx)
    if not ReplayRepo(ctx.conn).untouched(study.id):
        raise InvalidQuery("a listing can only be replaced before the study's first replay")
    return study


def _check_bands(bands: str, symbols: list[str] | None) -> None:
    if bands not in PICKS:
        raise InvalidQuery(f"bands must be large, mid-small or mix, not {bands!r}")
    if symbols and LARGE not in PICKS[bands]:
        raise InvalidQuery("listings named for a study stand in for its large half: ask for large or mix with them")


def _listings_json(picked: list[Picked]) -> str:
    return json.dumps([asdict(p) for p in picked])


def submit_create(ctx: AppContext, runner: JobRunner, first: date | None = None,
                  symbols: list[str] | None = None, bands: str = MIX) -> Job:
    """Create a study as a job: checking prices takes a minute or two. What can be refused
    without them is refused now."""
    _window(ctx, first)
    _check_bands(bands, symbols)
    lines: list[str] = []

    async def work(c: AppContext) -> StudyStatus:
        return await create_study(c, first, symbols, lines.append, bands)

    return runner.submit("replay", "Create replay study", work, subject="create", link="#ledgers",
                         progress=lambda: lines[-1] if lines else None)


def submit_replace(ctx: AppContext, runner: JobRunner, old: str, new: str) -> Job:
    _untouched(ctx)

    async def work(c: AppContext) -> StudyStatus:
        return await replace_listing(c, old, new)

    return runner.submit("replay", f"Replace {old} with {new} in the replay study", work, subject=f"replace {old}",
                         link="#ledgers")


def start_study(ctx: AppContext) -> StudyStatus:
    """Let the pacer submit replays whenever the PC is free, unless its models changed or the
    cutoff now allows none."""
    study = _require(ctx)
    why, _ = cannot_run(ctx, study)
    if why:
        raise InvalidQuery(why)
    ReplayRepo(ctx.conn).set_state(study.id, RUNNING)
    return _require_status(ctx)


def pause_study(ctx: AppContext, reason: str | None = None) -> StudyStatus:
    """No new replays; one already running finishes."""
    study = _require(ctx)
    ReplayRepo(ctx.conn).set_state(study.id, PAUSED, reason)
    return _require_status(ctx)


def retry_failed(ctx: AppContext, cell_id: int | None = None) -> int:
    """Failed cells (or the one) back in the queue, each with one more try; never one on or
    before the current cutoff, which the model may know."""
    study = _require(ctx)
    repo = ReplayRepo(ctx.conn)
    cutoff = pinned_cutoff(ctx, study.stages)
    failed = [c for c in repo.cells(study.id, FAILED) if cell_id is None or c.id == cell_id]
    if cell_id is not None and not failed:
        raise InvalidQuery(f"cell {cell_id} is not a failed cell of the current replay study")
    if cell_id is not None and cutoff and failed[0].as_of <= cutoff:
        raise InvalidQuery(f"{failed[0].as_of} is on or before the measured cutoff {cutoff}: a replay then could "
                           "see ahead")
    return sum(repo.retry_failed(study.id, c.id) for c in failed if not cutoff or c.as_of > cutoff)


def cannot_run(ctx: AppContext, study: StudyRow) -> tuple[str | None, ReplayCutoff]:
    """Why the study may not replay now, and the replay cutoff as it stands."""
    return _cannot_run(ctx, study.stages)


def study_status(ctx: AppContext) -> StudyStatus | None:
    repo = ReplayRepo(ctx.conn)
    study = repo.current()
    if study is None:
        return None
    counts = repo.counts(study.id)
    now = ctx.clock.utcnow()
    running = tuple(RunningCell(c.id, c.symbol, c.as_of, c.job_id, (now - c.started_at).total_seconds()
                                if c.started_at else 0.0) for c in repo.cells(study.id, RUNNING))
    failed = tuple(FailedCell(c.id, c.symbol, c.as_of, c.error, c.attempts) for c in repo.cells(study.id, FAILED))
    durations = repo.durations(study.id, RECENT_REPLAYS)
    pinned = pins(study.stages)
    return StudyStatus(
        study.id, study.state, study.paused_reason, study.created_at, study.first_day, study.last_day,
        tuple(Picked(**p) for p in json.loads(study.listings)), sum(counts.values()), counts.get(DONE, 0),
        counts.get(PENDING, 0), repo.retrying(study.id), running, failed,
        tuple(repo.failure_reasons(study.id, TOP_FAILURES)),
        sum(durations) / len(durations) if durations else None, pinned_cutoff(ctx, study.stages),
        study.waiting if study.state == RUNNING else None, study.polled_at, repo.untouched(study.id),
        score=score_study(ctx, study).interim(), stages={stage: p.profile for stage, p in pinned.items()},
        stage_models={stage: f"{p.provider} {p.model}" for stage, p in pinned.items() if p.model})


def _require(ctx: AppContext) -> StudyRow:
    study = ReplayRepo(ctx.conn).current()
    if study is None:
        raise InvalidQuery("no replay study yet: create one first")
    return study


def _require_status(ctx: AppContext) -> StudyStatus:
    status = study_status(ctx)
    assert status is not None
    return status


def _error_kind(error: str, symbol: str) -> str:
    """The error without what differs between cells, so one outage reads the same on each: its
    head (the text before the first colon, usually the error's type or source), without the
    symbol, quoted text, addresses or numbers."""
    head = error.split(":", 1)[0].replace(symbol, "~")
    # A quote opens only after a non-letter, so an apostrophe ("model's") never starts one.
    head = re.sub(r"(?<!\w)('[^']*'|\"[^\"]*\")", "''", head)
    head = re.sub(r"0x[0-9a-fA-F]+", "#", head)
    return re.sub(r"\d+", "#", head)[:80]


class ReplayPacer:
    """Submits the study's replays one at a time as research jobs, gives way to other work,
    and records how each ended. The queue is in the store, and so is what the pacer keeps
    between polls (jobs it stopped, a back-off), so a restart resumes where it was."""

    def __init__(self, ctx: AppContext, runner: JobRunner, scheduler: Scheduler | None = None) -> None:
        self._ctx = ctx
        self._runner = runner
        self._scheduler = scheduler
        self._load(None)
        self.waiting: str | None = None
        """Why the last poll held the next replay back, if it did."""

    def _load(self, raw: str | None) -> None:
        state = json.loads(raw) if raw else {}
        self._preempted: set[int] = set(state.get("preempted", ()))
        self._timed_out: dict[int, str] = {int(k): v for k, v in state.get("timed_out", {}).items()}
        """Jobs stopped as hung, and the error their cell gets."""
        self._run: list[tuple[int, str]] = [(c, k) for c, k in state.get("run", ())]
        """The failures in a row since the last replay that ended well: cell and error kind."""
        self._outage_since: datetime | None = _when(state.get("outage_since"))
        """While an outage is waited out, its failures cost no attempt."""
        self._outage_kind: str | None = state.get("outage_kind")
        """The error kind of an outage found by its failures alike; None for one found by any five."""
        self._outage_error: str | None = state.get("outage_error")
        self._backoffs: int = state.get("backoffs", 0)
        self._resume_at: datetime | None = _when(state.get("resume_at"))

    def _dump(self) -> str:
        return json.dumps({
            "preempted": sorted(self._preempted), "timed_out": {str(k): v for k, v in self._timed_out.items()},
            "run": self._run, "outage_since": _iso(self._outage_since) if self._outage_since else None,
            "outage_kind": self._outage_kind, "outage_error": self._outage_error, "backoffs": self._backoffs,
            "resume_at": _iso(self._resume_at) if self._resume_at else None})

    def check(self) -> Job | None:
        """One poll: record replays that ended, stop one that is in the way, then submit the
        next when nothing holds it back."""
        repo = ReplayRepo(self._ctx.conn)
        self.waiting = None
        study = repo.current()
        if study is None:
            return None
        self._load(study.pacer)
        try:
            return self._poll(repo, study)
        finally:
            repo.note_poll(study.id, self._ctx.clock.utcnow(), self.waiting, self._dump())

    def _poll(self, repo: ReplayRepo, study: StudyRow) -> Job | None:
        for cell in repo.cells(study.id, RUNNING):
            self._settle(repo, study, cell)
        study = repo.current()
        if running := repo.cells(study.id, RUNNING):
            self._make_way(repo, study, running)
            return None
        if study.state != RUNNING:
            return None
        cell = self._next_allowed(repo, study)
        if cell is None:
            return None
        if why := self.hold(repo, study):
            self.waiting = why
            return None
        try:
            job = submit_research(self._ctx, self._runner, cell.symbol, cell.as_of, automatic=True)
        except ServiceError as exc:
            self._failed(repo, study, cell, str(exc))
            return None
        repo.mark_running(cell.id, job.id, self._ctx.clock.utcnow())
        return job

    def _next_allowed(self, repo: ReplayRepo, study: StudyRow) -> CellRow | None:
        """The next cell the study's models may replay. A replay job resolves its models at run
        time, so the slots, probes and claims are read again before every submit: a change of
        models or an unprobed one pauses the study, and cells now on or before the cutoff stop
        for good."""
        if repo.next_pending(study.id) is None:
            return None
        why, rc = cannot_run(self._ctx, study)
        if why:
            repo.set_state(study.id, PAUSED, why)
            return None
        cutoff = pinned_cutoff(self._ctx, study.stages)
        if cutoff and (early := [c.id for c in repo.cells(study.id, PENDING) if c.as_of <= cutoff]):
            repo.stop_for_good(early, f"on or before the cutoff {cutoff} of {rc.set_by}", self._ctx.clock.utcnow())
        return repo.next_pending(study.id)

    def hold(self, repo: ReplayRepo, study: StudyRow) -> str | None:
        """Why a replay must not start now: ticks, manual work and the user always come first."""
        ctx = self._ctx
        now = ctx.clock.utcnow()
        if not ctx.config.automation.enabled:
            return "automation is off"
        if self._resume_at and now < self._resume_at:
            return (f"backing off: replays have kept failing since {self._outage_since.astimezone():%Y-%m-%d %H:%M} "
                    f"({self._backoffs} back-off{'' if self._backoffs == 1 else 's'}; the last error: "
                    f"{self._outage_error}); trying again at {self._resume_at.astimezone():%H:%M}")
        if busy := self._runner.current():
            return f"waiting for {busy[0].label} to finish"
        if elsewhere := held_elsewhere(ctx):
            return f"waiting for {elsewhere[0].job.replace('_', ' ')} running in another process"
        if (up := self._next_run()) and up.at - now <= self._expected(repo, study) + MARGIN:
            return f"a {KIND_LABELS[up.kind].lower()} is due at {up.at.astimezone():%H:%M}"
        if pc_in_use(ctx):
            return f"the PC is in use: replays wait for {ctx.config.schedule.idle_minutes} idle minutes"
        return None

    def _next_run(self):
        return self._scheduler.next_run() if self._scheduler else None

    @staticmethod
    def _expected(repo: ReplayRepo, study: StudyRow) -> timedelta:
        """The longest recent replay: a mean would let every slower one run into a tick."""
        durations = repo.durations(study.id, RECENT_REPLAYS)
        return timedelta(seconds=max(durations)) if durations else DEFAULT_REPLAY

    def _make_way(self, repo: ReplayRepo, study: StudyRow, running: list[CellRow]) -> None:
        """Stop a running replay that has hung (a failure), or that a scheduled run or other
        model work is waiting for (its cell goes back to pending at the next poll, costing
        nothing)."""
        replays = {c.job_id for c in running}
        longest = 3 * self._expected(repo, study)
        cap = max(longest, HARD_CAP)
        cap_why = "3 times the longest recent replay" if longest >= HARD_CAP else "the least any replay is given"
        now = self._ctx.clock.utcnow()
        for cell in running:
            job = self._runner.get(cell.job_id) if cell.job_id is not None else None
            if job is None or job.work is None or job.state not in ("queued", "running"):
                continue
            # From the job's own start: an adopted Activity retry may wait in the queue first.
            if job.state == "running" and job.started_at and now - job.started_at > cap:
                self._timed_out[job.id] = f"stopped after running over {cap // timedelta(minutes=1)} minutes ({cap_why})"
                self._runner.cancel(job.id)
            elif why := self._in_the_way(replays):
                self._preempted.add(job.id)
                self._runner.cancel(job.id)
                what = "replay" if job.automatic else "replay retried in Activity"
                self.waiting = (f"stopped the {what} of {cell.symbol} as of {cell.as_of.isoformat()} for {why}; "
                                "it goes back in the queue")

    def _in_the_way(self, replays: set[int | None]) -> str | None:
        other = next((j for j in self._runner.current() if j.id not in replays and j.kind in PREEMPTED_BY), None)
        if other:
            return other.label
        up = self._next_run()
        if up and up.at - self._ctx.clock.utcnow() <= PREEMPT_WITHIN:
            return f"the {KIND_LABELS[up.kind].lower()} due at {up.at.astimezone():%H:%M}"
        return None

    def _settle(self, repo: ReplayRepo, study: StudyRow, cell: CellRow) -> None:
        job = self._runner.get(cell.job_id) if cell.job_id is not None else None
        if job is not None and job.state in ("queued", "running"):
            if job.work is not None or self._alive_elsewhere(job.id):
                return
            repo.back_to_pending(cell.id)  # its server stopped without closing it
        elif job is None or job.state == "interrupted" or job.error == NOT_RUN:
            repo.back_to_pending(cell.id)  # cut off by a restart
        elif job.state == "done":
            run_id = getattr(job.result, "id", None) or ResearchRepo(self._ctx.conn).replay_run_id(cell.symbol,
                                                                                                  cell.as_of)
            repo.mark_done(cell.id, run_id, job.finished_at or self._ctx.clock.utcnow(), job.started_at)
            self._run, self._outage_since, self._backoffs, self._resume_at = [], None, 0, None
        elif job.state == "failed":
            self._failed(repo, study, cell, job.error or "failed")
        elif job.id in self._timed_out:
            self._failed(repo, study, cell, self._timed_out.pop(job.id))
        else:
            repo.back_to_pending(cell.id)
            if job.id in self._preempted:
                self._preempted.discard(job.id)
            elif self._ctx.config.automation.enabled:
                repo.set_state(study.id, PAUSED, f"the replay of {cell.symbol} as of {cell.as_of.isoformat()} was "
                                                 "cancelled in Activity, so the study paused: press Start to go on")
            # Cancelled by turning automation off: the pacer holds while it is off, and goes on after.

    def _alive_elsewhere(self, job_id: int) -> bool:
        pid = JobHistoryRepo(self._ctx.conn).pid(job_id)
        return pid is not None and pid != os.getpid() and pid_alive(pid)

    def _failed(self, repo: ReplayRepo, study: StudyRow, cell: CellRow, error: str) -> None:
        now = self._ctx.clock.utcnow()
        repo.mark_failed(cell.id, error, now)
        kind = _error_kind(error, cell.symbol)
        if self._outage_since:
            # The probe after a back-off failed: the outage goes on, whatever its error says now.
            if self._outage_kind in (None, kind):
                repo.refund([cell.id], MAX_REFUNDS)
            else:
                # A new error costs its cell the attempt, but five in a row are an outage too.
                self._run.append((cell.id, kind))
                if len(self._run) >= ANY_OUTAGE_AFTER:
                    repo.refund([cell_id for cell_id, _ in self._run], MAX_REFUNDS)
                    self._run, self._outage_kind = [], None
            self._back_off(error, now)
            return
        self._run.append((cell.id, kind))
        alike = self._run[-OUTAGE_AFTER:]
        if len(alike) == OUTAGE_AFTER and len({k for _, k in alike}) == 1:
            outage, self._outage_kind = alike, kind
        elif len(self._run) >= ANY_OUTAGE_AFTER:
            outage, self._outage_kind = self._run, None
        else:
            return
        repo.refund([cell_id for cell_id, _ in outage], MAX_REFUNDS)
        self._run, self._outage_since = [], now
        self._back_off(error, now)

    def _back_off(self, error: str, now: datetime) -> None:
        self._backoffs, self._outage_error = self._backoffs + 1, error
        self._resume_at = now + (BACKOFF if self._backoffs <= MAX_BACKOFFS else LONG_BACKOFF)

    async def run(self) -> None:
        while True:
            try:
                self.check()
            except Exception:
                # A bug in one poll must not end the study for the life of the server.
                log.exception("replay pacer poll failed")
            await self._ctx.clock.sleep(POLL_S)

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
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta

from oracle.jobs import pid_alive
from oracle.market.bars import major_unit
from oracle.services.activity_service import Job, JobRunner, submit_research
from oracle.services.context import AppContext
from oracle.services.errors import InvalidQuery, PriceUnavailable, ServiceError, UnknownSymbol
from oracle.services.jobs_service import held_elsewhere
from oracle.services.never_list import never_company
from oracle.services.prices_service import update_listing
from oracle.services.research_service import pc_in_use
from oracle.services.schedule_service import Scheduler
from oracle.services.tick_service import KIND_LABELS
from oracle.store.bars import BarRepo
from oracle.store.health import HealthRepo
from oracle.store.job_history import NOT_RUN, JobHistoryRepo
from oracle.store.replay import DONE, FAILED, PAUSED, PENDING, RUNNING, CellRow, ReplayRepo, StudyRow
from oracle.store.research import ResearchRepo
from oracle.store.universe import UniverseRepo
from oracle.universe.exchanges import EXCHANGES, exchange_country
from oracle.universe.listings import Listing, is_receipt, rank_listings

__all__ = ["Picked", "ReplayPacer", "StudyStatus", "create_study", "measured_cutoff", "pause_study", "pick_listings",
           "replace_listing", "retry_failed", "start_study", "study_status", "submit_create", "submit_replace"]

log = logging.getLogger(__name__)

REGIONS: dict[str, tuple[str, ...]] = {
    "US": ("United States",),
    "UK": ("United Kingdom",),
    "Europe": ("Germany", "France", "Netherlands", "Switzerland", "Spain", "Italy", "Sweden", "Denmark", "Belgium",
               "Finland", "Ireland", "Norway", "Austria", "Portugal"),
    "Japan": ("Japan",),
    "Asia": ("South Korea", "Taiwan", "Hong Kong", "Singapore"),
    "Emerging": ("India", "Brazil", "Mexico", "South Africa", "Indonesia"),
}
CURRENCIES: dict[str, frozenset[str]] = {
    "US": frozenset({"USD"}),
    "UK": frozenset({"GBP"}),
    "Europe": frozenset({"EUR", "CHF", "SEK", "DKK", "NOK"}),
    "Japan": frozenset({"JPY"}),
    "Asia": frozenset({"KRW", "TWD", "HKD", "SGD"}),
    "Emerging": frozenset({"INR", "BRL", "MXN", "ZAR", "IDR"}),
}
"""A region's line trades in one of these, so a New York ADR or a Berlin line never stands in."""
MAIN_BOARD_TIER = 2
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
"""One listing per slot: about 20, every region and sector the study needs."""
BANDS = ("Mega Cap", "Large Cap", "Mid Cap")
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
BACKOFF = timedelta(minutes=45)
MAX_BACKOFFS = 4
"""Back-offs in a row before the study pauses."""
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


def measured_cutoff(ctx: AppContext) -> date | None:
    """The first day the research slot's model cannot know about, as the cutoff probe measured
    it (replay-study 01). None until a probe has stored one."""
    return None


def _month_back(day: date) -> date:
    y, m = (day.year - 1, 12) if day.month == 1 else (day.year, day.month - 1)
    return date(y, m, min(day.day, calendar.monthrange(y, m)[1]))


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
    tier = EXCHANGES.get(listing.exchange or "", (None, 9))[1]
    if listing.delisted or tier > MAIN_BOARD_TIER or not listing.currency:
        return None
    country, currency = exchange_country(listing.exchange), major_unit(listing.currency)[0]
    return next((r for r, countries in REGIONS.items() if country in countries and currency in CURRENCIES[r]), None)


async def _price_problem(ctx: AppContext, symbol: str, first: date) -> str | None:
    """Why the listing's prices cannot score a study from `first` (fetching them when the store
    falls short), or None."""
    bars = BarRepo(ctx.conn)
    recent = ctx.clock.utcnow().date() - RECENT
    span = bars.span([symbol])
    if not span or span[0] > first or span[1] < recent:
        try:
            await update_listing(ctx, symbol)
        except (PriceUnavailable, UnknownSymbol) as exc:
            return str(exc)
        span = bars.span([symbol])
    if not span:
        return f"no prices for {symbol}"
    if span[0] > first:
        return f"{symbol}'s prices start {span[0].isoformat()}, after {first.isoformat()}"
    if span[1] < recent:
        return f"{symbol}'s last price is {span[1].isoformat()}: it no longer trades"
    return None


async def pick_listings(ctx: AppContext, first: date, say: Callable[[str], None] = lambda _: None) -> list[Picked]:
    """One listing per slot: the biggest band, then the most widely listed company, on its
    region's main board in its currency, that is not on the never list, a receipt or dead, and
    whose prices run from `first` to now (fetched here). A slot the universe cannot fill is
    left out, saying why."""
    universe = UniverseRepo(ctx.conn)
    dead = {s.upper() for s in HealthRepo(ctx.conn).dead_symbols()}
    picked: list[Picked] = []
    for region, sector in SLOTS:
        tries = 0
        for company in universe.largest_in(REGIONS[region], sector, BANDS):
            if tries == TRIES_PER_SLOT:
                break
            line = next((x for x in rank_listings(universe.company_listings(company.company_key), dead)
                         if _region_of(x) == region and x.symbol.upper() not in dead), None)
            if (line is None or any(p.symbol == line.symbol for p in picked) or is_receipt(line.name)
                    or never_company(ctx, line.symbol, user=True)):
                continue
            tries += 1
            say(f"{region} {sector}: checking {line.symbol}")
            if problem := await _price_problem(ctx, line.symbol, first):
                say(f"  passed over: {problem}")
                continue
            profile = universe.profile(line.symbol)
            picked.append(Picked(line.symbol, company.name, region, sector, profile.market_cap if profile else None))
            break
        else:
            say(f"{region} {sector}: nothing fits, left out")
    return picked


async def check_symbol(ctx: AppContext, symbol: str, first: date) -> Picked:
    """A listing the user named, held to the same rules as a picked one except its slot."""
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
    return Picked(resolved.symbol, resolved.name, region, profile.sector or "", profile.market_cap)


def _window(ctx: AppContext, first: date | None) -> tuple[date, date]:
    """The first and last replay day a new study may use, refusing what cannot be one."""
    if ReplayRepo(ctx.conn).any_running():
        raise InvalidQuery("a replay is running: wait for it, or pause the study and let it finish, first")
    cutoff = measured_cutoff(ctx)
    first = first or cutoff
    if first is None:
        raise InvalidQuery("no measured model cutoff is stored yet: give the first replay day (--from)")
    if cutoff and first < cutoff:
        raise InvalidQuery(f"{first.isoformat()} is before the model's measured cutoff ({cutoff.isoformat()}): "
                           "its replays could lean on what the model remembers")
    last = _month_back(ctx.clock.utcnow().date())
    if first > last:
        raise InvalidQuery(f"{first.isoformat()} leaves no replay a month of outcome: pick an earlier day")
    return first, last


async def create_study(ctx: AppContext, first: date | None = None, symbols: list[str] | None = None,
                       say: Callable[[str], None] = lambda _: None) -> StudyStatus:
    """A new study, paused: its listings (picked, or `symbols`) checked and saved, one cell
    per listing and month."""
    first, last = _window(ctx, first)
    if symbols:
        picked = [await check_symbol(ctx, s, first) for s in dict.fromkeys(symbols)]
    else:
        picked = await pick_listings(ctx, first, say)
    if not picked:
        raise InvalidQuery("no listing in the universe fits the spread: refresh the universe first")
    _window(ctx, first)  # a replay may have started while prices were fetched
    cells = replay_cells(ctx, [p.symbol for p in picked], first, last)
    ReplayRepo(ctx.conn).create(ctx.clock.utcnow(), min(d for _, d in cells), max(d for _, d in cells),
                                _listings_json(picked), cells, ALLOWED)
    return _require_status(ctx)


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
                          chosen.sector or listings[at].sector, chosen.market_cap)
    last = _month_back(ctx.clock.utcnow().date())
    cells = replay_cells(ctx, [p.symbol for p in listings], study.first_day, last)
    ReplayRepo(ctx.conn).replace_cells(study.id, min(d for _, d in cells), max(d for _, d in cells),
                                       _listings_json(listings), cells, ALLOWED)
    return _require_status(ctx)


def _untouched(ctx: AppContext) -> StudyRow:
    study = _require(ctx)
    if not ReplayRepo(ctx.conn).untouched(study.id):
        raise InvalidQuery("a listing can only be replaced before the study's first replay")
    return study


def _listings_json(picked: list[Picked]) -> str:
    return json.dumps([asdict(p) for p in picked])


def submit_create(ctx: AppContext, runner: JobRunner, first: date | None = None,
                  symbols: list[str] | None = None) -> Job:
    """Create a study as a job: checking prices takes a minute or two. What can be refused
    without them is refused now."""
    _window(ctx, first)
    lines: list[str] = []

    async def work(c: AppContext) -> StudyStatus:
        return await create_study(c, first, symbols, lines.append)

    return runner.submit("replay", "Create replay study", work, subject="create", link="#ledgers",
                         progress=lambda: lines[-1] if lines else None)


def submit_replace(ctx: AppContext, runner: JobRunner, old: str, new: str) -> Job:
    _untouched(ctx)

    async def work(c: AppContext) -> StudyStatus:
        return await replace_listing(c, old, new)

    return runner.submit("replay", f"Replace {old} with {new} in the replay study", work, subject=f"replace {old}",
                         link="#ledgers")


def start_study(ctx: AppContext) -> StudyStatus:
    """Let the pacer submit replays whenever the PC is free."""
    study = _require(ctx)
    ReplayRepo(ctx.conn).set_state(study.id, RUNNING)
    return _require_status(ctx)


def pause_study(ctx: AppContext, reason: str | None = None) -> StudyStatus:
    """No new replays; one already running finishes."""
    study = _require(ctx)
    ReplayRepo(ctx.conn).set_state(study.id, PAUSED, reason)
    return _require_status(ctx)


def retry_failed(ctx: AppContext, cell_id: int | None = None) -> int:
    """Failed cells (or the one) back in the queue, each with one more try."""
    study = _require(ctx)
    moved = ReplayRepo(ctx.conn).retry_failed(study.id, cell_id)
    if cell_id is not None and not moved:
        raise InvalidQuery(f"cell {cell_id} is not a failed cell of the current replay study")
    return moved


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
    return StudyStatus(
        study.id, study.state, study.paused_reason, study.created_at, study.first_day, study.last_day,
        tuple(Picked(**p) for p in json.loads(study.listings)), sum(counts.values()), counts.get(DONE, 0),
        counts.get(PENDING, 0), repo.retrying(study.id), running, failed,
        tuple(repo.failure_reasons(study.id, TOP_FAILURES)),
        sum(durations) / len(durations) if durations else None, measured_cutoff(ctx),
        study.waiting if study.state == RUNNING else None, study.polled_at, repo.untouched(study.id))


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
    """The error without what differs between cells, so one outage reads the same on each."""
    return re.sub(r"\d+", "#", error.replace(symbol, "~"))[:80]


class ReplayPacer:
    """Submits the study's replays one at a time as research jobs, gives way to other work,
    and records how each ended. The queue is in the store, so a restart resumes where it was;
    what it holds in memory (jobs it stopped, a back-off) only shapes the next few polls."""

    def __init__(self, ctx: AppContext, runner: JobRunner, scheduler: Scheduler | None = None) -> None:
        self._ctx = ctx
        self._runner = runner
        self._scheduler = scheduler
        self._preempted: set[int] = set()
        self._streak: list[int] = []
        self._streak_kind: str | None = None
        self._outage: str | None = None
        """The error kind of an outage being waited out: its failures cost no attempt."""
        self._outage_error: str | None = None
        self._backoffs = 0
        self._resume_at: datetime | None = None
        self.waiting: str | None = None
        """Why the last poll held the next replay back, if it did."""

    def check(self) -> Job | None:
        """One poll: record replays that ended, stop one that is in the way, then submit the
        next when nothing holds it back."""
        repo = ReplayRepo(self._ctx.conn)
        self.waiting = None
        study = repo.current()
        if study is None:
            return None
        job = self._poll(repo, study)
        repo.note_poll(study.id, self._ctx.clock.utcnow(), self.waiting)
        return job

    def _poll(self, repo: ReplayRepo, study: StudyRow) -> Job | None:
        for cell in repo.cells(study.id, RUNNING):
            self._settle(repo, study, cell)
        study = repo.current()
        if running := repo.cells(study.id, RUNNING):
            self._make_way(running)
            return None
        if study.state != RUNNING:
            return None
        cell = repo.next_pending(study.id)
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

    def hold(self, repo: ReplayRepo, study: StudyRow) -> str | None:
        """Why a replay must not start now: ticks, manual work and the user always come first."""
        ctx = self._ctx
        now = ctx.clock.utcnow()
        if not ctx.config.automation.enabled:
            return "automation is off"
        if self._resume_at and now < self._resume_at:
            return (f"backing off after replays kept failing alike ({self._outage_error}): trying again at "
                    f"{self._resume_at.astimezone():%H:%M}")
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

    def _make_way(self, running: list[CellRow]) -> None:
        """Stop a running replay that a scheduled run or other model work is waiting for; its
        cell goes back to pending at the next poll, costing nothing."""
        replays = {c.job_id for c in running}
        for cell in running:
            job = self._runner.get(cell.job_id) if cell.job_id is not None else None
            if job is None or job.work is None or job.state not in ("queued", "running"):
                continue
            if why := self._in_the_way(replays):
                self._preempted.add(job.id)
                self._runner.cancel(job.id)
                self.waiting = f"stopped the replay of {cell.symbol} as of {cell.as_of.isoformat()} for {why}"

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
            repo.mark_done(cell.id, run_id, job.finished_at or self._ctx.clock.utcnow())
            self._streak, self._streak_kind, self._outage, self._backoffs = [], None, None, 0
        elif job.state == "failed":
            self._failed(repo, study, cell, job.error or "failed")
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
        if kind == self._outage:
            repo.refund([cell.id])
            self._back_off(repo, study, error, now)
            return
        if kind != self._streak_kind:
            self._streak, self._streak_kind, self._outage = [], kind, None
        self._streak.append(cell.id)
        if len(self._streak) >= OUTAGE_AFTER:
            repo.refund(self._streak)
            self._outage, self._streak = kind, []
            self._back_off(repo, study, error, now)

    def _back_off(self, repo: ReplayRepo, study: StudyRow, error: str, now: datetime) -> None:
        self._backoffs, self._outage_error = self._backoffs + 1, error
        if self._backoffs > MAX_BACKOFFS:
            hours = MAX_BACKOFFS * BACKOFF / timedelta(hours=1)
            repo.set_state(study.id, PAUSED, f"replays kept failing alike for about {hours:.0f} hours (the last: "
                                             f"{error}): check the model, then press Start")
            self._outage, self._backoffs, self._resume_at = None, 0, None
            return
        self._resume_at = now + BACKOFF

    async def run(self) -> None:
        while True:
            try:
                self.check()
            except Exception:
                # A bug in one poll must not end the study for the life of the server.
                log.exception("replay pacer poll failed")
            await self._ctx.clock.sleep(POLL_S)

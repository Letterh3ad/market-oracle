"""The replay study: research replayed at past dates after the model's cutoff, over a fixed
spread of listings, one replay at a time in the PC's free time. It never starts by itself; the
pacer submits a replay only after Start, and only when nothing else wants the machine."""

from __future__ import annotations

import calendar
import json
import logging
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta

from oracle.services.activity_service import Job, JobRunner, submit_research
from oracle.services.context import AppContext
from oracle.services.errors import InvalidQuery, ServiceError
from oracle.services.jobs_service import held_elsewhere
from oracle.services.never_list import never_company
from oracle.services.replay_score import Interim, score_study
from oracle.services.research_service import pc_in_use
from oracle.services.schedule_service import Scheduler
from oracle.services.tick_service import KIND_LABELS
from oracle.store.bars import BarRepo
from oracle.store.health import HealthRepo
from oracle.store.job_history import NOT_RUN
from oracle.store.replay import DONE, FAILED, PAUSED, PENDING, RUNNING, CellRow, ReplayRepo, StudyRow
from oracle.store.research import ResearchRepo
from oracle.store.universe import UniverseRepo
from oracle.universe.listings import is_receipt

__all__ = ["Picked", "ReplayPacer", "StudyStatus", "create_study", "measured_cutoff", "pause_study", "pick_listings",
           "replay_days", "retry_failed", "start_study", "study_status"]

log = logging.getLogger(__name__)

REGIONS: dict[str, tuple[str, ...]] = {
    "US": ("United States",),
    "UK": ("United Kingdom",),
    "Europe": ("Germany", "France", "Netherlands", "Switzerland", "Spain", "Italy", "Sweden", "Denmark", "Belgium",
               "Finland", "Ireland"),
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
"""One listing per slot: about 20, every region and sector the study needs."""
BANDS = ("Mega Cap", "Large Cap", "Mid Cap")

DEFAULT_REPLAY = timedelta(minutes=5)
"""The expected length of a replay before any has finished."""
MARGIN = timedelta(minutes=10)
MEAN_OF = 10
"""Replays the expected length is the mean of."""
AUTO_RETRIES = 1
FAILURES_TO_PAUSE = 3
"""Straight failures that pause the study: the model or the network is down, not one listing."""
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
    """Pending cells that failed once and wait for their automatic retry."""
    running: tuple[RunningCell, ...]
    failed: tuple[FailedCell, ...]
    """Failed for good, until Retry."""
    top_failures: tuple[tuple[str, int], ...]
    mean_replay_s: float | None
    cutoff: date | None
    waiting: str | None = None
    """Why the pacer holds the next replay back; known only to the server running it."""
    score: Interim | None = None

    @property
    def complete(self) -> bool:
        return self.pending == 0 and not self.running

    def to_json(self) -> dict:
        """Plain JSON types, the same for the CLI's --json and the GUI's endpoint."""
        return json.loads(json.dumps({**asdict(self), "complete": self.complete}, default=_iso))


def _iso(value: date | datetime) -> str:
    return value.isoformat()


def measured_cutoff(ctx: AppContext) -> date | None:
    """The first day the research slot's model cannot know about, as the cutoff probe measured
    it (replay-study 01). None until a probe has stored one."""
    return None


def replay_days(first: date, today: date) -> list[date]:
    """The first trading day of each month from `first` to a month before `today`, so every
    replay has at least a month of outcome."""
    last = _month_back(today)
    days, y, m = [], first.year, first.month
    while (d := _first_trading_day(y, m)) <= last:
        if d >= first:
            days.append(d)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return days


def _first_trading_day(year: int, month: int) -> date:
    d = date(year, month, 2 if month == 1 else 1)  # New Year's Day is a holiday everywhere
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def _month_back(day: date) -> date:
    y, m = (day.year - 1, 12) if day.month == 1 else (day.year, day.month - 1)
    return date(y, m, min(day.day, calendar.monthrange(y, m)[1]))


def pick_listings(ctx: AppContext, first_day: date) -> list[Picked]:
    """One listing per slot: the biggest band, then the most widely listed company, that is
    not on the never list, a receipt, dead, or first priced after `first_day`. A slot the
    universe has nothing for is left out."""
    universe, bars = UniverseRepo(ctx.conn), BarRepo(ctx.conn)
    dead = {s.upper() for s in HealthRepo(ctx.conn).dead_symbols()}
    picked: list[Picked] = []
    for region, sector in SLOTS:
        for row in universe.largest_in(REGIONS[region], sector, BANDS):
            if row.symbol.upper() in dead or any(p.symbol == row.symbol for p in picked) or is_receipt(row.name):
                continue
            if (span := bars.span([row.symbol])) and span[0] > first_day:
                continue
            if never_company(ctx, row.symbol, user=True):
                continue
            picked.append(Picked(row.symbol, row.name, region, sector, row.market_cap))
            break
    return picked


def create_study(ctx: AppContext, first: date | None = None) -> StudyStatus:
    """A new study, paused: its listings picked now and saved, one cell per listing and day,
    oldest day first so early results already have their longest outcomes."""
    repo = ReplayRepo(ctx.conn)
    if (current := repo.current()) and current.state == RUNNING:
        raise InvalidQuery("the current replay study is running: pause it before creating another")
    cutoff = measured_cutoff(ctx)
    first = first or cutoff
    if first is None:
        raise InvalidQuery("no measured model cutoff is stored yet: give the first replay day (--from)")
    if cutoff and first < cutoff:
        raise InvalidQuery(f"{first.isoformat()} is before the model's measured cutoff ({cutoff.isoformat()}): "
                           "its replays could lean on what the model remembers")
    today = ctx.clock.utcnow().date()
    days = replay_days(first, today)
    if not days:
        raise InvalidQuery(f"no month starts between {first.isoformat()} and a month ago: pick an earlier day")
    picked = pick_listings(ctx, days[0])
    if not picked:
        raise InvalidQuery("no listing in the universe fits the spread: refresh the universe first")
    listings = json.dumps([asdict(p) for p in picked])
    repo.create(ctx.clock.utcnow(), days[0], days[-1], listings, [(p.symbol, d) for d in days for p in picked])
    return _require_status(ctx)


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


def study_status(ctx: AppContext, waiting: str | None = None) -> StudyStatus | None:
    repo = ReplayRepo(ctx.conn)
    study = repo.current()
    if study is None:
        return None
    counts = repo.counts(study.id)
    now = ctx.clock.utcnow()
    running = tuple(RunningCell(c.id, c.symbol, c.as_of, c.job_id, (now - c.started_at).total_seconds()
                                if c.started_at else 0.0) for c in repo.cells(study.id, RUNNING))
    failed = tuple(FailedCell(c.id, c.symbol, c.as_of, c.error, c.attempts) for c in repo.cells(study.id, FAILED))
    retrying = sum(1 for c in repo.cells(study.id, PENDING) if c.error)
    durations = repo.durations(study.id, MEAN_OF)
    return StudyStatus(
        study.id, study.state, study.paused_reason, study.created_at, study.first_day, study.last_day,
        tuple(Picked(**p) for p in json.loads(study.listings)), sum(counts.values()), counts.get(DONE, 0),
        counts.get(PENDING, 0), retrying, running, failed, tuple(repo.failure_reasons(study.id, TOP_FAILURES)),
        sum(durations) / len(durations) if durations else None, measured_cutoff(ctx),
        waiting if study.state == RUNNING else None, score_study(ctx, study).interim())


def _require(ctx: AppContext) -> StudyRow:
    study = ReplayRepo(ctx.conn).current()
    if study is None:
        raise InvalidQuery("no replay study yet: create one first")
    return study


def _require_status(ctx: AppContext) -> StudyStatus:
    status = study_status(ctx)
    assert status is not None
    return status


class ReplayPacer:
    """Submits the study's replays one at a time as research jobs, and records how each ended.
    Everything it knows is in the store, so a restart resumes where it was."""

    def __init__(self, ctx: AppContext, runner: JobRunner, scheduler: Scheduler | None = None) -> None:
        self._ctx = ctx
        self._runner = runner
        self._scheduler = scheduler
        self._failed_in_row = 0
        self.waiting: str | None = None
        """Why the last poll held the next replay back, if it did."""

    def check(self) -> Job | None:
        """One poll: record replays that ended, then submit the next when nothing holds it back."""
        repo = ReplayRepo(self._ctx.conn)
        self.waiting = None
        study = repo.current()
        if study is None:
            return None
        for cell in repo.cells(study.id, RUNNING):
            self._settle(repo, study, cell)
        study = repo.current()
        if study.state != RUNNING or repo.cells(study.id, RUNNING):
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
        if not ctx.config.automation.enabled:
            return "automation is off"
        if busy := self._runner.current():
            return f"waiting for {busy[0].label} to finish"
        if elsewhere := held_elsewhere(ctx):
            return f"waiting for {elsewhere[0].job.replace('_', ' ')} running in another process"
        if self._scheduler and (up := self._scheduler.upcoming()):
            if up.at - ctx.clock.utcnow() <= self._expected(repo, study) + MARGIN:
                return f"a {KIND_LABELS[up.kind].lower()} is due at {up.at.astimezone():%H:%M}"
        if pc_in_use(ctx):
            return f"the PC is in use: replays wait for {ctx.config.schedule.idle_minutes} idle minutes"
        return None

    @staticmethod
    def _expected(repo: ReplayRepo, study: StudyRow) -> timedelta:
        durations = repo.durations(study.id, MEAN_OF)
        return timedelta(seconds=sum(durations) / len(durations)) if durations else DEFAULT_REPLAY

    def _settle(self, repo: ReplayRepo, study: StudyRow, cell: CellRow) -> None:
        job = self._runner.get(cell.job_id) if cell.job_id is not None else None
        if job is not None and job.state in ("queued", "running") and job.work is not None:
            return
        if job is None or job.state in ("queued", "running", "interrupted") or job.error == NOT_RUN:
            repo.back_to_pending(cell.id)  # cut off by a restart
        elif job.state == "done":
            run_id = getattr(job.result, "id", None) or ResearchRepo(self._ctx.conn).replay_run_id(cell.symbol,
                                                                                                  cell.as_of)
            repo.mark_done(cell.id, run_id, job.finished_at or self._ctx.clock.utcnow())
            self._failed_in_row = 0
        elif job.state == "failed":
            self._failed(repo, study, cell, job.error or "failed")
        else:
            repo.back_to_pending(cell.id)
            repo.set_state(study.id, PAUSED, f"the replay of {cell.symbol} as of {cell.as_of.isoformat()} was "
                                             "cancelled, so the study paused: press Start to go on")

    def _failed(self, repo: ReplayRepo, study: StudyRow, cell: CellRow, error: str) -> None:
        repo.mark_failed(cell.id, error, self._ctx.clock.utcnow(), requeue=cell.attempts < AUTO_RETRIES)
        self._failed_in_row += 1
        if self._failed_in_row >= FAILURES_TO_PAUSE:
            self._failed_in_row = 0
            repo.set_state(study.id, PAUSED, f"paused after {FAILURES_TO_PAUSE} replays failed in a row "
                                             f"(the last: {error}): check the model, then press Start")

    async def run(self) -> None:
        while True:
            try:
                self.check()
            except Exception:
                # A bug in one poll must not end the study for the life of the server.
                log.exception("replay pacer poll failed")
            await self._ctx.clock.sleep(POLL_S)

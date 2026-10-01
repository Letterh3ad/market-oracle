"""The typed-event study on the testbed (next-signals 05, spec 8): 8-K items 2.02, 1.01 and 2.01
and insider-buy clusters, each event's cumulative sector-excess return from the next session's
open at 1, 5, 20 and 60 trading days, train 2010-2019 against confirm 2020 to today, pre-registered
(DECISIONS 2026-10-01). Reads the research database only and keeps each result there."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import date

from oracle.analysts.event_study import HORIZONS, PRIMARY, EventRow, event_cars, passes, summarise
from oracle.research import insider
from oracle.services.context import AppContext
from oracle.services.errors import InvalidQuery
from oracle.services.insider_service import InsiderData, issuer_data
from oracle.services.signal_test_service import (
    CONFIRM_START, EVENT_KEY, TRAIN_END, TRAIN_KEY, TRAIN_ONLY, load_panel, missing_share,
)
from oracle.services.testbed_service import START, Testbed, research_context
from oracle.store.db import to_utc_iso
from oracle.store.signal_tests import SignalTestRepo

__all__ = ["EVENT_TYPES", "EventStudy", "EventType", "KEY", "event_studies", "run_event_study", "run_and_keep_events"]

KEY = EVENT_KEY
WAIT_S = 5.0
EVENT_TYPES = {
    "2.02": "8-K item 2.02: results of operations",
    "1.01": "8-K item 1.01: material definitive agreement",
    "2.01": "8-K item 2.01: acquisition or disposal of assets",
    insider.CLUSTER: "Insider-buy cluster: 3 or more officers and directors buying within 30 days",
}
CAVEAT = ("Entry at the open of the session after the filing's acceptance day, so a results 8-K's own reaction is "
          "mostly before it (this measures the drift after). The t is clustered by decision day; it does not "
          "allow for windows overlapping across nearby days, so at 20 and 60 days it is optimistic. Train 2010-2014 "
          "misses 25-35% of member-days; renamed members have no CIK; share classes with one CIK repeat events. "
          "Only the confirm window at the primary horizon gives a verdict.")


@dataclass(frozen=True)
class EventType:
    name: str
    label: str
    train: tuple[EventRow, ...]
    confirm: tuple[EventRow, ...]
    """Per horizon; empty for a train-only run."""
    verdict: str
    passed: bool


@dataclass(frozen=True)
class EventStudy:
    types: tuple[EventType, ...]
    primary: int
    horizons: tuple[int, ...]
    first_day: date
    last_day: date
    trades_through: date | None
    """The last day of the latest stored quarter of insider trades."""
    missing_train: float | None
    missing_confirm: float | None
    run_at: str
    train_only: bool = False
    caveat: str = CAVEAT
    id: int | None = None


def _verdict(train: EventRow | None, confirm: EventRow | None) -> str:
    if train is None or not train.mean or confirm is None or confirm.t is None:
        return "INCONCLUSIVE"
    return "PASS" if passes(train, confirm) else "FAIL"


class _Stopped(Exception):
    pass


async def run_event_study(ctx: AppContext, *, end: date | None = None, train_only: bool = False,
                          stop: Callable[[], bool] | None = None, busy: Callable[[], str | None] | None = None,
                          on_progress: Callable[[str], None] = lambda _: None) -> EventStudy | None:
    """The study as pre-registered over the testbed to `end` (default today); reads only. `train_only`
    loads nothing after TRAIN_END and computes no confirm. None when `stop`."""
    say = on_progress

    async def turn(label: str) -> None:
        if stop and stop():
            raise _Stopped
        while busy and (other := busy()):
            say(f"waiting for {other}")
            await ctx.clock.sleep(WAIT_S)
            if stop and stop():
                raise _Stopped
        say(label)

    with research_context(ctx) as r:
        tb = Testbed(r)
        try:
            await turn("loading the testbed")
            if train_only:
                end = min(end or TRAIN_END, TRAIN_END)
            p = load_panel(tb, end, say=say, keep_priced_duplicate=True)
            if not any(p.members):
                raise InvalidQuery("the testbed has no members: build it first")
            await turn("loading the filings and insider trades")
            tickers = sorted(set().union(*p.members))
            data = issuer_data(r, tickers, p.days[-1])
            types = []
            for name, label in EVENT_TYPES.items():
                await turn(f"event study: {name}")
                types.append(_study(p, data, name, label, train_only))
        except _Stopped:
            return None
    last = p.days[-1]
    return EventStudy(tuple(types), PRIMARY, HORIZONS, p.days[0], last, data.through,
                      missing_share(p, START, TRAIN_END), None if train_only else missing_share(p, CONFIRM_START, last),
                      to_utc_iso(ctx.clock.utcnow()), train_only)


def _events(p, data: InsiderData, name: str) -> dict[str, list[int]]:
    """ticker -> decision day indexes (the session after each event's known day)."""
    out = {}
    for s, d in data.by_ticker.items():
        days = insider.clusters(d.buys) if name == insider.CLUSTER else d.items.get(name, [])
        idx = [i for k in days if (i := insider.first_after(p.days, k)) < len(p.days)]
        if idx:
            out[s] = idx
    return out


def _study(p, data: InsiderData, name: str, label: str, train_only: bool) -> EventType:
    cars = event_cars(p, _events(p, data, name), HORIZONS)
    train, confirm = [], []
    for h in HORIZONS:
        tr = [(p.days[i], x) for i, x in cars[h] if START <= p.days[i] and p.days[i + h] <= TRAIN_END]
        co = [(p.days[i], x) for i, x in cars[h] if p.days[i] >= CONFIRM_START]
        train.append(summarise(tr, h))
        if not train_only:
            confirm.append(summarise(co, h))
    k = HORIZONS.index(PRIMARY)
    verdict = TRAIN_ONLY if train_only else _verdict(train[k], confirm[k])
    return EventType(name, label, tuple(train), tuple(confirm), verdict, verdict == "PASS")


def keep_event_study(ctx: AppContext, study: EventStudy) -> EventStudy:
    raw = json.dumps(asdict(study), default=str)
    verdict = ", ".join(f"{t.name} {t.verdict}" for t in study.types)
    with research_context(ctx) as r:
        new_id = SignalTestRepo(r.conn).add(KEY + (TRAIN_KEY if study.train_only else ""), verdict, raw, study.run_at)
    return _load(raw, new_id)


async def run_and_keep_events(ctx: AppContext, **kw) -> EventStudy | None:
    s = await run_event_study(ctx, **kw)
    return None if s is None else keep_event_study(ctx, s)


def event_studies(ctx: AppContext) -> dict[str, EventStudy]:
    """{"full": newest full result, "train": newest train-only one}, those that exist."""
    with research_context(ctx) as r:
        rows = SignalTestRepo(r.conn).latest()
    return {("train" if row.signal.endswith(TRAIN_KEY) else "full"): _load(row.result, row.id) for row in rows
            if row.signal.removesuffix(TRAIN_KEY) == KEY}


def _rows(rows: list[dict]) -> tuple[EventRow, ...]:
    return tuple(EventRow(**x) for x in rows)


def _load(raw: str, row_id: int) -> EventStudy:
    d = json.loads(raw)
    types = tuple(EventType(**{**t, "train": _rows(t["train"]), "confirm": _rows(t["confirm"])}) for t in d["types"])
    day = lambda x: date.fromisoformat(x) if x else None  # noqa: E731
    return EventStudy(**{**d, "types": types, "horizons": tuple(d["horizons"]), "first_day": day(d["first_day"]),
                         "last_day": day(d["last_day"]), "trades_through": day(d["trades_through"]), "id": row_id})

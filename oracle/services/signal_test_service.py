"""The general pre-registered signal test (next-signals 02, DECISIONS 2026-10-01): any daily
per-company score from the registry against sector-excess returns on the testbed, chosen on
2010-2019 (train, reported only) and judged on 2020 to today (confirm, the verdict). Reads the
research database through the testbed's API and keeps each result there. Laya's test keeps its
own harness; both share predictive.py's IC, t and verdict."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import date

from oracle.analysts.predictive import T_BAR
from oracle.analysts.signal_test import (
    Panel, SegmentRow, Signal, WindowResult, daily_ics, momentum_12_1, overall, reversal_1m, unseen_text,
    window_result,
)
from oracle.broker.walk_forward import segments
from oracle.research.training_cutoffs import training_cutoff
from oracle.services import fund_compare_service, trend_service
from oracle.services.context import AppContext
from oracle.services.errors import InvalidQuery
from oracle.services.insider_service import INSIDER_BUYS
from oracle.services.lab_registry import require_committed
from oracle.services.testbed_service import START, Testbed, research_context
from oracle.services.xbrl_service import FUNDAMENTAL_SIGNALS
from oracle.store.db import to_utc_iso
from oracle.store.signal_tests import SignalTestRepo

__all__ = ["CAVEAT", "CONFIRM_START", "SIGNALS", "TRAIN_END", "SignalTest", "check_signal", "keep_signal_test",
           "load_panel", "run_and_keep", "run_signal_test", "signal_tests"]

TRAIN_END = date(2019, 12, 31)
TRAIN_ONLY = "TRAIN ONLY"
"""The verdict of a train-only run: it reads nothing after TRAIN_END, so it judges nothing."""
TRAIN_KEY = ":train"
"""Appended to a train-only result's stored signal name, so it never hides the full result."""
CONFIRM_START = date(2020, 1, 1)
EVENT_KEY = "event_study"
OTHER_TESTS = frozenset({EVENT_KEY, trend_service.KEY, fund_compare_service.KEY})
"""Kept beside the signal tests in the same table, but not signal tests."""
"""The typed-event study keeps its results beside the signals' under this name."""
CHUNK = 250
"""Decision days scored between two checks for a stop or live work."""
WAIT_S = 5.0
CAVEAT = ("2010-2014 misses 25-35% of member-days (delisted, acquired and renamed members), so the train window is "
          "survivorship-weighted and a pass driven by it is provisional; sectors are today's GICS; membership after "
          "the snapshot's last change repeats that day's list; a member that stops trading inside its window drops "
          "out of that day. Only the confirm window gives a verdict.")

SIGNALS: dict[str, Signal] = {s.name: s for s in (
    Signal("momentum_12_1", "12-1 momentum: the past year to the last close, leaving out its last month", (20,),
           momentum_12_1),
    Signal("reversal_1m", "1-month reversal: minus the last month's return to the last close", (5,), reversal_1m),
    *FUNDAMENTAL_SIGNALS,
    INSIDER_BUYS,
)}


def check_signal(name: str, registry: dict[str, Signal] | None = None) -> Signal:
    registry = SIGNALS if registry is None else registry
    if name not in registry:
        raise InvalidQuery(f"no signal {name!r}; the registry has {', '.join(sorted(registry))}")
    return registry[name]


@dataclass(frozen=True)
class SignalTest:
    signal: str
    label: str
    horizons: tuple[int, ...]
    model: str | None
    cutoff: date | None
    """Only later days count (a model-read signal); None for a price signal."""
    train: tuple[WindowResult, ...]
    """Per horizon; reported, never a verdict."""
    confirm: tuple[WindowResult, ...]
    train_verdict: str
    verdict: str
    """The confirm window's: PASS only when every horizon passes."""
    passed: bool
    first_day: date
    last_day: date
    tickers: int
    """Members on some day of the calendar."""
    snapshot: str | None
    run_at: str
    caveat: str = CAVEAT
    id: int | None = None
    train_only: bool = False
    """Bars to TRAIN_END only and no confirm (a rule still being chosen must not see it)."""
    bar: float = T_BAR
    """The t its registration set (kept results from before the lab needed t above 2)."""
    bar_inclusive: bool = False

    @property
    def verdict_text(self) -> str:
        """The verdict, saying so when every failed horizon's IC was above 0 but too small for its window to see."""
        failed = [w for w in self.confirm if w.verdict == "FAIL"]
        if self.verdict == "FAIL" and all(w.unseen for w in failed):
            return unseen_text(max(w.observed_power for w in failed))
        return self.verdict


def load_panel(tb: Testbed, end: date | None = None, start: date = START,
               say: Callable[[str], None] = lambda _: None, keep_priced_duplicate: bool = False) -> Panel:
    """Every member's adjusted bars on the trading calendar, each day's members and sectors. Of a
    duplicate pair both in on a day, the later listing is dropped, or with `keep_priced_duplicate`
    the one without a bar that day (the later one when both or neither have one)."""
    days = tb.calendar(start, end)
    members = [frozenset(tb.members_on(d)) for d in days]
    p = Panel(days, members, tb.sectors())
    tickers = sorted(set().union(*members)) if members else []
    for n, t in enumerate(tickers, 1):
        if n % 100 == 0:
            say(f"loading bars ({n}/{len(tickers)})")
        p.add(t, tb.bars(t, start, end))
    pairs = [pair for pair, _ in tb.duplicates()]
    for i, m in enumerate(members):
        both = [(a, b) for a, b in pairs if a in m and b in m]
        if both:
            drop = {a if keep_priced_duplicate and p.priced(b, i) and not p.priced(a, i) else b for a, b in both}
            p.members[i] = m - drop
    return p


def missing_share(p: Panel, start: date, end: date) -> float | None:
    """Member-days from `start` to `end` without a bar."""
    total = priced = 0
    for i, d in enumerate(p.days):
        if start <= d <= end:
            total += len(p.members[i])
            priced += sum(p.priced(s, i) for s in p.members[i])
    return round(1 - priced / total, 4) if total else None


class _Stopped(Exception):
    pass


async def run_signal_test(ctx: AppContext, name: str, *, registry: dict[str, Signal] | None = None,
                          end: date | None = None, train_only: bool = False, stop: Callable[[], bool] | None = None,
                          busy: Callable[[], str | None] | None = None,
                          on_progress: Callable[[str], None] = lambda _: None) -> SignalTest | None:
    """The test of `name` as pre-registered, over the testbed to `end` (default today); reads only.
    Between chunks of days it waits while `busy` names live work, and returns None when `stop`.
    `train_only` loads nothing after TRAIN_END and computes no confirm. Refused unless the signal's
    pre-registration is committed; it runs the registered horizons (never the code's) and its
    verdict needs the bar the registration froze."""
    signal = check_signal(name, registry)
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
        reg = require_committed(r, [signal.name])[signal.name]
        bar, inclusive = (T_BAR, False) if reg.bar is None else (reg.bar, reg.bar_inclusive)
        horizons = reg.horizons
        tb = Testbed(r)
        try:
            await turn("loading the testbed")
            if train_only:
                end = min(end or TRAIN_END, TRAIN_END)
            p = load_panel(tb, end, say=say, keep_priced_duplicate=signal.keep_priced_duplicate)
            if not any(p.members):
                raise InvalidQuery("the testbed has no members: build it first")
            if signal.prepare:
                await turn(f"loading what {signal.name} reads")
                signal.prepare(r, p)
            last = p.days[-1]
            cutoff = training_cutoff(signal.model) if signal.model else None
            train_spans, confirm_spans = segments(START, min(TRAIN_END, last)), segments(CONFIRM_START, last)
            missing = {"train": missing_share(p, START, TRAIN_END), "confirm": missing_share(p, CONFIRM_START, last)}
            train, confirm = [], []
            for h in horizons:
                ics: dict = {}
                for i in range(1, len(p.days), CHUNK):
                    await turn(f"{signal.name}, {h}-day horizon: {p.days[i]}")
                    ics.update(daily_ics(p, signal.score, h, i, i + CHUNK))
                train.append(window_result("train", ics, train_spans, h, missing["train"], cutoff, bar, inclusive))
                if not train_only:
                    confirm.append(window_result("confirm", ics, confirm_spans, h, missing["confirm"], cutoff, bar,
                                                 inclusive))
        except _Stopped:
            return None
        snap = tb.snapshot()
    verdict = TRAIN_ONLY if train_only else overall([w.verdict for w in confirm])
    return SignalTest(signal.name, signal.label, horizons, signal.model, cutoff, tuple(train), tuple(confirm),
                      overall([w.verdict for w in train]), verdict, verdict == "PASS", p.days[0], last,
                      len(set().union(*p.members)),
                      f"{snap.source} at {(snap.revision or 'unknown')[:7]}, last change {snap.last_day}" if snap else None,
                      to_utc_iso(ctx.clock.utcnow()), train_only=train_only, bar=bar, bar_inclusive=inclusive)


def keep_signal_test(ctx: AppContext, test: SignalTest) -> SignalTest:
    raw = json.dumps(asdict(test), default=str)
    with research_context(ctx) as r:
        key = test.signal + (TRAIN_KEY if test.train_only else "")
        new_id = SignalTestRepo(r.conn).add(key, test.verdict, raw, test.run_at)
    return _load(raw, new_id)


async def run_and_keep(ctx: AppContext, name: str, **kw) -> SignalTest | None:
    """Run and keep; None when stopped."""
    t = await run_signal_test(ctx, name, **kw)
    return None if t is None else keep_signal_test(ctx, t)


def signal_tests(ctx: AppContext, train_only: bool = False) -> dict[str, SignalTest]:
    """The newest kept full result of each signal, or with `train_only` its newest train-only one."""
    with research_context(ctx) as r:
        rows = SignalTestRepo(r.conn).latest()
    return {row.signal.removesuffix(TRAIN_KEY): _load(row.result, row.id) for row in rows
            if row.signal.endswith(TRAIN_KEY) == train_only and row.signal.removesuffix(TRAIN_KEY) not in OTHER_TESTS}


def _windows(rows: list[dict]) -> tuple[WindowResult, ...]:
    return tuple(WindowResult(**{**w, "start": date.fromisoformat(w["start"]), "end": date.fromisoformat(w["end"]),
                                 "segments": tuple(SegmentRow(**{**s, "start": date.fromisoformat(s["start"]),
                                                                 "end": date.fromisoformat(s["end"])})
                                                   for s in w["segments"])}) for w in rows)


def _load(raw: str, test_id: int) -> SignalTest:
    d = json.loads(raw)
    return SignalTest(**{**d, "horizons": tuple(d["horizons"]), "train": _windows(d["train"]),
                         "confirm": _windows(d["confirm"]), "first_day": date.fromisoformat(d["first_day"]),
                         "last_day": date.fromisoformat(d["last_day"]),
                         "cutoff": date.fromisoformat(d["cutoff"]) if d["cutoff"] else None, "id": test_id})

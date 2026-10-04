from __future__ import annotations

import argparse
import asyncio

from oracle.cli.history import PROGRESS_S, _every
from oracle.services.context import AppContext
from oracle.services.event_study_service import EventStudy, event_studies, run_and_keep_events
from oracle.services.signal_test_service import SIGNALS, SignalTest, run_and_keep, signal_tests


def register(parser: argparse.ArgumentParser) -> None:
    parser.description = ("pre-registered signal tests on the testbed: chosen on 2010-2019 (train, reported only), "
                          "judged on 2020 to today (confirm, the verdict)")
    sub = parser.add_subparsers(dest="signals_command", required=True)
    listing = sub.add_parser("list", description="the registry's signals and each one's newest kept result")
    listing.set_defaults(handler=run_list)
    test = sub.add_parser("test", description="run one signal's test on the testbed (minutes) and keep the result in "
                                              "the research database; it does not wait for the app's live work")
    test.add_argument("name", choices=sorted(SIGNALS))
    test.add_argument("--train-only", action="store_true",
                      help="2010-2019 alone: reads no bar after 2019 and computes no confirm, for choosing a rule")
    test.set_defaults(handler=run_test)
    events = sub.add_parser("events", description="the pre-registered typed-event study (8-K items 2.02, 1.01, 2.01 "
                                                  "and insider-buy clusters): mean sector-excess return from the next "
                                                  "session's open at 1, 5, 20, 60 days, train and confirm; kept in "
                                                  "the research database")
    events.add_argument("--train-only", action="store_true", help="2010-2019 alone: no confirm")
    events.add_argument("--show", action="store_true", help="print the newest kept result instead of running")
    events.set_defaults(handler=run_events)


def run_list(ctx: AppContext, args: argparse.Namespace) -> int:
    kept, trains = signal_tests(ctx), signal_tests(ctx, train_only=True)
    for s in SIGNALS.values():
        t = kept.get(s.name)
        result = f"{t.verdict_text} (train {t.train_verdict}), run {t.run_at[:16]} UTC" if t else "not run yet"
        horizons = ", ".join(str(h) for h in s.horizons)
        print(f"{s.name:<16} {horizons:>6} d  {result}")
        print(f"  {s.label}" + (f"; model {s.model}" if s.model else ""))
        if tr := trains.get(s.name):
            print(f"  train only: {tr.train_verdict}, run {tr.run_at[:16]} UTC")
    return 0


def run_test(ctx: AppContext, args: argparse.Namespace) -> int:
    t = asyncio.run(run_and_keep(ctx, args.name, train_only=args.train_only, on_progress=_every(PROGRESS_S)))
    print_test(t)
    return 0


def _num(x: float | None, fmt: str) -> str:
    return "-" if x is None else format(x, fmt)


def print_test(t: SignalTest) -> None:
    print(f"{t.signal}: {t.label}")
    print(f"Testbed: {t.first_day} to {t.last_day}, {t.tickers} members; {t.snapshot or 'no snapshot'}")
    if t.cutoff:
        print(f"Model-read: only days after the training cutoff {t.cutoff} count")
    print(f"{'window':<8} {'period':<23} {'h':>3} {'IC':>8} {'t':>6} {'days':>5} {'names':>6} {'eff.days':>8} "
          f"{'power':>7} {'obs.power':>9} {'missing':>8} {'turnover':>8} {'b/even':>7} {'counted':>8}  verdict")
    for w in (*t.train, *t.confirm):
        note = " (reported only)" if w.window == "train" else ""
        print(f"{w.window:<8} {w.start} {w.end} {w.horizon:>3} {_num(w.ic, '+.4f'):>8} {_num(w.t, '.2f'):>6} "
              f"{w.days:>5} {_num(w.names, '.0f'):>6} {w.effective_days:>8.0f} {_num(w.power, '.4f'):>7} "
              f"{_num(w.observed_power, '.4f'):>9} {_num(w.missing_share, '.1%'):>8} {_num(w.turnover, '.1%'):>8} "
              f"{_num(w.break_even, '.2%'):>7} "
              f"{f'{w.positive}+/{w.counted}':>8}  {w.verdict_text}{note}")
    for w in (*t.train, *t.confirm):
        print(f"{w.window} segments, {w.horizon}-day horizon:")
        for s in w.segments:
            print(f"  {s.start} to {s.end}  IC {_num(s.ic, '+.4f'):>8}  t {_num(s.t, '.2f'):>6}  {s.days:>4} days  "
                  f"{'counted' if s.counted else s.why}")
    print("Power is the formula's (names, effective days, horizon); obs.power is the window's own spread, the "
          f"smallest IC it could see at its bar, t {t.bar:.2f}")
    print("turnover: share of the top fifth's equal-weight book replaced each horizon; b/even: the one-way cost per "
          "trade that takes its sector-excess edge to zero (- when it has none)")
    print("Train only: confirm not computed, no verdict" if t.train_only else f"Verdict (confirm): {t.verdict_text}")
    print(f"Caveat: {t.caveat}")


def run_events(ctx: AppContext, args: argparse.Namespace) -> int:
    if args.show:
        kept = event_studies(ctx).get("train" if args.train_only else "full")
        if kept is None:
            print("No event study kept yet")
            return 0
        print_events(kept)
        return 0
    print_events(asyncio.run(run_and_keep_events(ctx, train_only=args.train_only, on_progress=_every(PROGRESS_S))))
    return 0


def print_events(s: EventStudy) -> None:
    print(f"Typed-event study, run {s.run_at[:16]} UTC: testbed {s.first_day} to {s.last_day}; insider trades through "
          f"{s.trades_through or 'none stored'}; missing share train {_num(s.missing_train, '.1%')}, confirm "
          f"{_num(s.missing_confirm, '.1%')}")
    print(f"Mean cumulative sector-excess return from the next session's open; t clustered by decision day; primary "
          f"horizon {s.primary} d (*)")
    for t in s.types:
        print(f"{t.name}: {t.label}")
        print(f"  {'window':<8} {'h':>3} {'events':>7} {'days':>6} {'mean CAR':>9} {'t':>6} {'up':>6}")
        for w, rows in (("train", t.train), ("confirm", t.confirm)):
            for r in rows:
                mark = " *" if r.horizon == s.primary else ""
                print(f"  {w:<8} {r.horizon:>3} {r.n:>7} {r.days:>6} {_num(r.mean, '+.2%'):>9} {_num(r.t, '.2f'):>6} "
                      f"{r.positive:>6}{mark}")
        print(f"  Verdict: {t.verdict}")
    print(f"PASS: confirm t above 2 at {s.primary} d in the train mean's sign. Caveat: {s.caveat}")

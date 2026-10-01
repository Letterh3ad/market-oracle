from __future__ import annotations

import argparse
import asyncio

from oracle.cli.history import PROGRESS_S, _every
from oracle.services.context import AppContext
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
    test.set_defaults(handler=run_test)


def run_list(ctx: AppContext, args: argparse.Namespace) -> int:
    kept = signal_tests(ctx)
    for s in SIGNALS.values():
        t = kept.get(s.name)
        result = f"{t.verdict} (train {t.train_verdict}), run {t.run_at[:16]} UTC" if t else "not run yet"
        horizons = ", ".join(str(h) for h in s.horizons)
        print(f"{s.name:<16} {horizons:>6} d  {result}")
        print(f"  {s.label}" + (f"; model {s.model}" if s.model else ""))
    return 0


def run_test(ctx: AppContext, args: argparse.Namespace) -> int:
    t = asyncio.run(run_and_keep(ctx, args.name, on_progress=_every(PROGRESS_S)))
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
          f"{'power':>7} {'obs.power':>9} {'missing':>8} {'counted':>8}  verdict")
    for w in (*t.train, *t.confirm):
        note = " (reported only)" if w.window == "train" else ""
        print(f"{w.window:<8} {w.start} {w.end} {w.horizon:>3} {_num(w.ic, '+.4f'):>8} {_num(w.t, '.2f'):>6} "
              f"{w.days:>5} {_num(w.names, '.0f'):>6} {w.effective_days:>8.0f} {_num(w.power, '.4f'):>7} "
              f"{_num(w.observed_power, '.4f'):>9} {_num(w.missing_share, '.1%'):>8} "
              f"{f'{w.positive}+/{w.counted}':>8}  {w.verdict}{note}")
    for w in (*t.train, *t.confirm):
        print(f"{w.window} segments, {w.horizon}-day horizon:")
        for s in w.segments:
            print(f"  {s.start} to {s.end}  IC {_num(s.ic, '+.4f'):>8}  t {_num(s.t, '.2f'):>6}  {s.days:>4} days  "
                  f"{'counted' if s.counted else s.why}")
    print(f"Verdict (confirm): {t.verdict}")
    print(f"Caveat: {t.caveat}")

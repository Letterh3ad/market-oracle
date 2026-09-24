from __future__ import annotations

import argparse
import asyncio
import json
import sys

from oracle.cli import cutoff
from oracle.cli.common import iso_day
from oracle.services.context import AppContext
from oracle.services.replay_score import Group, HorizonScore, Score, replay_report, update_study_prices
from oracle.services.replay_service import (
    StudyStatus, create_study, pause_study, replace_listing, retry_failed, start_study, study_status,
)


def register(parser: argparse.ArgumentParser) -> None:
    parser.description = ("Research replayed at past dates over a fixed spread of listings, one replay at a time "
                          "while `oracle gui` runs and the PC is free. It never starts by itself.")
    sub = parser.add_subparsers(dest="replay_command", required=True)
    create = sub.add_parser("create", help="pick the listings and dates; the study starts paused")
    create.add_argument("--from", dest="first", type=iso_day, default=None, metavar="YYYY-MM-DD",
                        help="the first replay day (default: the day after the measured cutoff)")
    create.add_argument("--symbols", type=_symbols, default=None, metavar="A,B,...",
                        help="these listings instead of the picked spread")
    create.set_defaults(handler=_create)
    replace = sub.add_parser("replace", help="swap one listing before the study's first replay")
    replace.add_argument("old", help="the listing to drop")
    replace.add_argument("new", help="the listing to take its place")
    replace.set_defaults(handler=_replace)
    sub.add_parser("start", help="let replays run in free time").set_defaults(handler=_start)
    sub.add_parser("pause", help="no new replays; a running one finishes").set_defaults(handler=_pause)
    status = sub.add_parser("status", help="progress, failures and the mean replay time")
    status.add_argument("--json", action="store_true", help="print the status as JSON")
    status.set_defaults(handler=_status)
    retry = sub.add_parser("retry", help="put failed cells back in the queue")
    retry.add_argument("cell", type=int, nargs="?", default=None, help="one cell's id (default: every failed cell)")
    retry.set_defaults(handler=_retry)
    report = sub.add_parser("report", help="score the verdicts against what prices did next, fetching missing bars "
                                           "first")
    report.add_argument("--json", action="store_true", help="print the report as JSON")
    report.set_defaults(handler=_report)
    cutoff.add_commands(sub)


def _symbols(text: str) -> list[str]:
    return [s.strip() for s in text.split(",") if s.strip()]


def _create(ctx: AppContext, args: argparse.Namespace) -> int:
    print("Checking each listing's prices (fetching what is missing); this takes a minute or two.")
    s = asyncio.run(create_study(ctx, args.first, args.symbols, print))
    print(f"Created replay study {s.id}: {len(s.listings)} listings, {s.total} replays from "
          f"{s.first_day.isoformat()} to {s.last_day.isoformat()}. It is paused: `oracle replay start` runs it.")
    print_listings(s)
    if s.left_out:
        print(f"Left out, nothing fits: {'; '.join(s.left_out)}. `oracle replay replace` can swap a listing in.")
    return 0


def _replace(ctx: AppContext, args: argparse.Namespace) -> int:
    s = asyncio.run(replace_listing(ctx, args.old, args.new))
    print(f"Replaced {args.old} with {args.new}: {len(s.listings)} listings, {s.total} replays.")
    print_listings(s)
    return 0


def _start(ctx: AppContext, args: argparse.Namespace) -> int:
    start_study(ctx)
    print("Started: replays run one at a time while `oracle gui` runs and nothing else needs the PC.")
    return 0


def _pause(ctx: AppContext, args: argparse.Namespace) -> int:
    pause_study(ctx)
    print("Paused: no new replays start; one already running finishes.")
    return 0


def _retry(ctx: AppContext, args: argparse.Namespace) -> int:
    n = retry_failed(ctx, args.cell)
    print(f"{n} failed cell{'' if n == 1 else 's'} back in the queue.")
    return 0


def _status(ctx: AppContext, args: argparse.Namespace) -> int:
    s = study_status(ctx)
    if args.json:
        print(json.dumps(s.to_json() if s else None, indent=2))
    elif s is None:
        print("No replay study yet: `oracle replay create --from YYYY-MM-DD`.")
    else:
        print_status(s)
    return 0


def print_listings(s: StudyStatus) -> None:
    for p in s.listings:
        print(f"  {p.symbol:<12}{p.region:<10}{p.sector:<24}{p.market_cap or ''}  {p.name}")


def print_status(s: StudyStatus) -> None:
    state = s.state + (f" ({s.paused_reason})" if s.paused_reason else "")
    print(f"Replay study {s.id}, {s.first_day.isoformat()} to {s.last_day.isoformat()}: {state}")
    print(f"  {s.done} done, {s.pending} queued ({s.retrying} to retry), {len(s.running)} running, "
          f"{len(s.failed)} failed of {s.total}")
    print(f"  listings: {', '.join(p.symbol for p in s.listings)}")
    mean = f"{s.mean_replay_s / 60:.1f} min" if s.mean_replay_s is not None else "none finished yet"
    print(f"  mean replay time: {mean}")
    if s.polled_at:
        print(f"  pacer last polled {s.polled_at.astimezone():%Y-%m-%d %H:%M}"
              + (f", waiting: {s.waiting}" if s.waiting else ""))
    else:
        print("  the pacer has not polled yet: it runs inside `oracle gui`")
    for c in s.running:
        print(f"  running: {c.symbol} as of {c.as_of.isoformat()} for {c.age_s / 60:.0f} min")
    for reason, n in s.top_failures:
        print(f"  {n} x {reason}")
    for c in s.failed:
        print(f"  failed cell {c.id}: {c.symbol} as of {c.as_of.isoformat()}: {c.error}")
    if s.complete:
        print("  complete: every replay is done")
    elif s.finished:
        print("  finished, with failed cells: `oracle replay retry` queues them again")


def _report(ctx: AppContext, args: argparse.Namespace) -> int:
    if replay_report(ctx) is None:
        print("null" if args.json else "No replay study yet: `oracle replay create --from YYYY-MM-DD`.")
        return 0
    for o in asyncio.run(update_study_prices(ctx)):
        line = f"Prices for {o.symbol}: " + (f"failed ({o.error})" if o.error else f"skipped ({o.skipped})"
                                             if o.skipped else f"{o.added} new bars ({o.source})")
        print(line, file=sys.stderr if args.json else sys.stdout)
    score = replay_report(ctx)
    assert score is not None
    if args.json:
        print(json.dumps(score.to_json(), indent=2))
    else:
        print_report(score)
    return 0


def _pct(v: float | None, signed: bool = False) -> str:
    return "n/a" if v is None else f"{v:+.2%}" if signed else f"{v:.0%}"


def _rate(v: float | None, ci: tuple[float, float] | None) -> str:
    return _pct(v) + (f" (95% {ci[0]:.0%} to {ci[1]:.0%})" if ci else "")


def print_report(s: Score) -> None:
    print(f"Replay study {s.study_id} score, {s.first_day.isoformat()} to {s.last_day.isoformat()} "
          f"(as of {s.today.isoformat()})")
    print(f"  {s.verdicts} verdicts, {s.no_verdict} without a verdict, {s.failed} failed, {s.to_run} still to run, "
          f"of {s.total} replays")
    m = s.mix
    confidence = "n/a" if m.mean_confidence is None else f"{m.mean_confidence:.2f}"
    print(f"  Verdict mix: {m.buy} buy, {m.hold} hold, {m.sell} sell ({_pct(m.buy_share)} buys), "
          f"mean confidence {confidence}")
    for h in s.horizons:
        print_horizon(h)
    if s.refetch_listings:
        print(f"  Due a whole-window refetch: {', '.join(s.refetch_listings)}")
    checked = f"{s.prices_checked_at:%Y-%m-%d %H:%M} UTC" if s.prices_checked_at else "never"
    failed = f"; failed: {', '.join(s.price_failures)}" if s.price_failures else ""
    print(f"  Study prices last checked: {checked}{failed}")
    print("Limits:")
    for line in s.limits:
        print(f"  - {line}")


def print_horizon(h: HorizonScore) -> None:
    print(f"  {h.months} month{'' if h.months == 1 else 's'}: {h.scored} scored, {h.pending} pending, {h.unpriced} "
          f"without prices; hit rate {_rate(h.hit_rate, h.hit_ci)}, {_rate(h.hit_rate_vs_basket, h.hit_vs_basket_ci)} "
          f"against the basket")
    print(f"    no-skill baseline on the called windows: {_pct(h.base_rate_up)} rose and "
          f"{_pct(h.base_rate_beat_basket)} beat the basket, so this call mix would hit {_pct(h.expected_hit_rate)} "
          f"({_pct(h.expected_hit_rate_vs_basket)} against the basket) by chance")
    print(f"    {'':<14}{'n':>4}{'hit':>7}{'hit rel':>11}{'mean':>10}{'mean rel':>11}{'confidence':>12}")
    for g in (*h.by_verdict, *h.by_band):
        print_group(g)
    print("    (rel: against the basket, which mixes the listings' currencies; bands: buys and sells only, as "
          "called, a sell's sign flipped)")


def print_group(g: Group) -> None:
    confidence = "n/a" if g.mean_confidence is None else f"{g.mean_confidence:.2f}"
    print(f"    {g.name:<14}{g.n:>4}{_pct(g.hit_rate):>7}{_pct(g.hit_rate_vs_basket):>11}"
          f"{_pct(g.mean_return, True):>10}{_pct(g.mean_vs_basket, True):>11}{confidence:>12}")

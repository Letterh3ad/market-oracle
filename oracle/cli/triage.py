"""`oracle lab osap|osap-fetch|osap-import|map|triage|power|table`: the funnel's triage and power
stages on Open Source Asset Pricing (research-lab 05)."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from oracle.services.context import AppContext
from oracle.services.triage_service import (
    TRIAGE_T, StageEntry, fetch_osap, import_osap, map_method, osap_status, run_power, run_triage, stage_table,
)

_BY = "who runs it (default user), e.g. autopilot"


def add_commands(sub) -> None:
    sub.add_parser("osap", description="Open Source Asset Pricing data held for triage: release, signals, months, "
                                       "imports, and when the compute window opens").set_defaults(handler=run_osap)
    sub.add_parser("osap-fetch", description="download OSAP's pinned release files (about 100 MB) and import them; "
                                             "compute window only, resumes a part file").set_defaults(handler=run_fetch)
    p = sub.add_parser("osap-import", description="import OSAP's files from a folder (SignalDoc.csv, "
                                                  "PredictorPortsFull.csv, the large-cap screen zip)")
    p.add_argument("folder", type=Path)
    p.set_defaults(handler=run_import)
    p = sub.add_parser("map", description="tie a method in the index to its OSAP signal (SignalDoc's acronym)")
    p.add_argument("ref")
    p.add_argument("signal", nargs="?")
    p.add_argument("--clear", action="store_true", help="untie it")
    p.add_argument("--by", default="user", help=_BY)
    p.set_defaults(handler=run_map)
    p = sub.add_parser("triage", description=f"funnel stage 1: the mapped signal's large-cap long-short return since "
                                             f"2000 must be positive at t {TRIAGE_T:g} (with the original sample and "
                                             "publication split); a method OSAP lacks is triaged by hand")
    p.add_argument("ref")
    p.add_argument("--by-hand", choices=("pass", "stop"), help="the literature's verdict, for a method OSAP lacks")
    p.add_argument("--note", default="", help="the evidence (needed by hand)")
    p.add_argument("--by", default="user", help=_BY)
    p.set_defaults(handler=run_triage_cmd)
    p = sub.add_parser("power", description="funnel stage 2: could the testbed see the literature's effect? A method "
                                            "it cannot see stops as could not see")
    p.add_argument("ref")
    p.add_argument("--strategy", action="store_true", help="a strategy: yearly excess over the index")
    p.add_argument("--horizon", type=int, default=20, help="a signal's horizon in trading days (default 20)")
    p.add_argument("--expected", type=float, help="the expected IC (signal) or yearly excess as a share (strategy); "
                                                  "default from OSAP's large-cap spread")
    p.add_argument("--tracking-error", type=float, help="a strategy's yearly tracking error against the index")
    p.add_argument("--source", default="", help="where the expected size comes from")
    p.add_argument("--by", default="user", help=_BY)
    p.set_defaults(handler=run_power_cmd)
    sub.add_parser("table", description="every method with an OSAP signal or a stage run: its newest triage and "
                                        "power results").set_defaults(handler=run_table)


def run_osap(ctx: AppContext, args: argparse.Namespace) -> int:
    s = osap_status(ctx)
    print(f"OSAP release {s.release}: " + (", ".join(f"{n} {k}" for k, n in sorted(s.signals.items()))
                                           or "nothing imported"))
    for v in s.variants:
        print(f"  {v.variant}: {v.signals} signals, {v.rows} signal-months, {v.first} to {v.last}")
    for i in s.imports:
        print(f"  imported {i.file} ({i.what}, {i.rows} rows) at {i.at[:16].replace('T', ' ')}")
    print(f"Files in {s.folder}: {', '.join(s.on_disk) or 'none'}")
    print(f"Downloads run in {s.window}: " + ("open now" if s.window_open else f"opens {s.next_window}"))
    return 0


def _imports(done) -> None:
    for i in done:
        print(f"Imported {i.file} ({i.what}): {i.rows} rows")


def run_fetch(ctx: AppContext, args: argparse.Namespace) -> int:
    _imports(asyncio.run(fetch_osap(ctx, say=lambda s: print(f"  {s}"))))
    return 0


def run_import(ctx: AppContext, args: argparse.Namespace) -> int:
    _imports(import_osap(ctx, args.folder))
    return 0


def run_map(ctx: AppContext, args: argparse.Namespace) -> int:
    name = map_method(ctx, args.ref, None if args.clear else args.signal, args.by)
    print(f"{args.ref}: OSAP {name}" if name else f"{args.ref}: no OSAP signal")
    return 0


def print_entry(ref: str, e: StageEntry) -> None:
    print(f"{ref} {e.stage}: {e.outcome.upper()} ({e.status_from} -> {e.status_to}): {e.summary}")
    d = e.detail
    for key in ("original", "large"):
        if key in d:
            spans = "; ".join(f"{s['label']} {s['mean']:+.2f} t {s['t'] or 0:.2f} ({s['n']} m)"
                              for s in d[key]["spans"] if s["mean"] is not None)
            decay = "" if d[key]["decay"] is None else f"; decay {d[key]['decay']:.0%}"
            print(f"  {'original portfolio' if key == 'original' else 'large caps'}: {spans}{decay}")
    if (paper := d.get("paper")) and paper.get("return") is not None:
        print(f"  paper ({paper['authors']} {paper['year']}): {paper['return']:+.2f}%/month"
              + (f" t {paper['t']:.2f}" if paper.get("t") is not None else ""))
    if "formula_ic" in d and d["formula_ic"] is not None:
        print(f"  names-and-days formula: {d['formula_ic']:.4f} (it leaves out cross-name correlation); "
              f"expected from {d['expected_from']}")


def run_triage_cmd(ctx: AppContext, args: argparse.Namespace) -> int:
    print_entry(args.ref, run_triage(ctx, args.ref, args.by, args.by_hand, args.note))
    return 0


def run_power_cmd(ctx: AppContext, args: argparse.Namespace) -> int:
    e = run_power(ctx, args.ref, "strategy" if args.strategy else "signal", args.horizon, args.expected,
                  args.tracking_error, source=args.source, by=args.by)
    print_entry(args.ref, e)
    return 0


def run_table(ctx: AppContext, args: argparse.Namespace) -> int:
    rows = stage_table(ctx)
    if not rows:
        print("No method has an OSAP signal or a stage run yet.")
        return 0
    print(f"{'method':<8}{'status':<14}{'OSAP':<14}{'triage':<8}{'power':<8}name")
    for r in rows:
        print(f"{r.ref:<8}{r.status:<14}{r.signal or '-':<14}{r.triage.outcome if r.triage else '-':<8}"
              f"{r.power.outcome if r.power else '-':<8}{r.name}")
        for e in (r.triage, r.power):
            if e:
                print(f"    {e.stage}: {e.summary}")
    return 0

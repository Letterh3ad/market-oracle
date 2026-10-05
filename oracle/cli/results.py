"""`oracle lab table`: the research lab's results table (research-lab 06), as text or as the JSON the
weekly page (research-lab 08) reads."""

from __future__ import annotations

import argparse
import json
import textwrap

from oracle.services.context import AppContext
from oracle.services.results_service import (
    FUNNEL_FILTER, STAGES, VERDICTS, ResultRow, ResultsTable, SourceTag, TestLine, results_table, table_json,
)

EVIDENCE_CHARS = 200


def add_commands(sub) -> None:
    p = sub.add_parser("table", description="every method in the literature index and every registered test, once "
                                            "each: sources and quality, evidence, the stage reached, triage and "
                                            "power, train and confirm against the bar, turnover and break-even "
                                            "cost, verdict and notes; deaths counted by stage")
    p.add_argument("--family", help="only this family, as the index names it")
    p.add_argument("--stage", help=f"only this stage: {', '.join(STAGES)}, or {FUNNEL_FILTER} (any but candidate)")
    p.add_argument("--verdict", help=f"only this verdict: {', '.join(VERDICTS)}")
    p.add_argument("--json", action="store_true", help="the table as JSON (the weekly page reads it)")
    p.set_defaults(handler=run_table)


def _cite(s: SourceTag) -> str:
    who = " ".join(x for x in (s.authors, str(s.year) if s.year else "") if x)
    return f"{who or s.title} ({s.quality})"


def _figure(measure: str, x: float) -> str:
    return f"{x:+.4f}" if measure == "IC" else f"{x:+.2%}"


def _line(x: TestLine) -> str:
    head = " ".join(p for p in (f"h{x.horizon}" if x.horizon is not None else "", x.measure) if p)
    figures = [f"{name} {text}" for name, text in (
        ("train", None if x.train is None else _figure(x.measure, x.train)),
        ("confirm", None if x.confirm is None else _figure(x.measure, x.confirm)),
        ("t", None if x.t is None else f"{x.t:.2f}"),
        ("power", None if x.power is None else f"{x.power:.4f}"),
        ("DSR", None if x.dsr is None else f"{x.dsr:.3f}"),
        ("turnover", None if x.turnover is None else f"{x.turnover:.1%}"),
        ("break-even", None if x.break_even is None else f"{x.break_even:.2%}")) if text is not None]
    return f"    {' '.join([head, *figures])}: {x.verdict}"


def _print_row(r: ResultRow) -> None:
    print(f"{r.ref or '-':<9}{r.stage:<12}{r.verdict:<15}{r.quality or '-':<15}{r.name}")
    facts = [r.family, f"status {r.status}" if r.status else "",
             f"sources: {', '.join(_cite(s) for s in r.sources)}" if r.sources else ""]
    if any(facts):
        print(f"  {'; '.join(f for f in facts if f)}")
    if r.evidence:
        print(f"  evidence: {textwrap.shorten(r.evidence, EVIDENCE_CHARS, placeholder=' ...')}")
    if r.result:
        print(f"  result: {r.result}")
    stage = [f"OSAP {r.osap}" if r.osap else "", f"triage {r.triage.outcome}: {r.triage.summary}" if r.triage else ""]
    if any(stage):
        print(f"  {'; '.join(s for s in stage if s)}")
    if r.power:
        print(f"  power {r.power.outcome}: {r.power.summary}")
    for t in r.tests:
        print(f"  test {t.key} ({t.kind}, bar {t.bar}): {t.verdict}; registration: oracle lab show {t.key}")
        for x in t.lines:
            print(_line(x))
    for u in r.tested_under:
        print(f"  test {u.key}: tested under {u.ref} ({u.stage}, {u.verdict})")
    for k in r.missing_tests:
        print(f"  test {k}: not in the registry")
    for n in r.notes:
        print(f"  note: {n.at[:10]} {n.mark} by {n.by}" + (f": {n.note}" if n.note else ""))


def print_table(t: ResultsTable) -> None:
    given = ", ".join(f"{k} {v}" for k, v in t.filters.items() if v)
    deaths = ", ".join(f"{k} {n}" for k, n in t.deaths.items())
    print(f"{len(t.rows)} rows{f' ({given})' if given else ''}. Deaths by stage: {deaths}")
    print(f"{'method':<9}{'stage':<12}{'verdict':<15}{'quality':<15}name")
    for r in t.rows:
        _print_row(r)
    print("Links: notes: oracle lab method REF; a test's registration: oracle lab show KEY. Stage unrecorded: a "
          "verdict from before the funnel recorded where it stopped.")


def run_table(ctx: AppContext, args: argparse.Namespace) -> int:
    t = results_table(ctx, args.family, args.stage, args.verdict)
    if args.json:
        print(json.dumps(table_json(t), indent=2))
    else:
        print_table(t)
    return 0

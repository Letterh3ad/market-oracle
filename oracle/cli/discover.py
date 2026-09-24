from __future__ import annotations

import argparse
import asyncio
import textwrap

from oracle.services.context import AppContext
from oracle.services.discovery_chat_service import (
    AnswerView, add_to_discovered, answer, ask, question, questions, set_auto,
)
from oracle.services.discovery_service import DiscoveryRun, Validation, discover

WIDTH = 96


def register(parser: argparse.ArgumentParser) -> None:
    parser.description = ("find companies related to the watched ones, on your themes or in market-mover news, "
                          "and add them to the \"discovered\" watchlist, up to [discovery] per_tick")
    parser.set_defaults(handler=run)
    sub = parser.add_subparsers(dest="discover_command")
    p = sub.add_parser("ask", help="ask which companies fit a question; answers with a shortlist")
    p.add_argument("question", help='e.g. "which European companies gain from grid batteries?"')
    p.add_argument("--refines", type=int, metavar="QUESTION_ID",
                   help="a follow-up narrowing that question, e.g. \"only mid caps\"")
    _model_flag(p)
    p.set_defaults(handler=run_ask)
    p = sub.add_parser("rerun", help="answer a saved question again and say what changed")
    p.add_argument("question_id", type=int)
    _model_flag(p)
    p.set_defaults(handler=run_rerun)
    sub.add_parser("questions", help="list saved questions").set_defaults(handler=run_questions)
    p = sub.add_parser("show", help="a saved question and its answers")
    p.add_argument("question_id", type=int)
    p.set_defaults(handler=run_show)
    p = sub.add_parser("add", help="put a shortlisted company on the discovered list")
    p.add_argument("answer_id", type=int)
    p.add_argument("rank", type=int, help="its number on the shortlist")
    p.set_defaults(handler=run_add)
    p = sub.add_parser("auto", help="switch a question's auto mode: its answers add the shortlisted companies that "
                                    "pass validation to the discovered list, up to [discovery] per_question")
    p.add_argument("question_id", type=int)
    p.add_argument("switch", choices=("on", "off"))
    p.set_defaults(handler=run_auto)


def _model_flag(p: argparse.ArgumentParser) -> None:
    p.add_argument("--model", metavar="PROFILE", default=None,
                   help="answer on this model profile instead of the [models.slots] discovery one")


def run(ctx: AppContext, args: argparse.Namespace) -> int:
    print_run(asyncio.run(discover(ctx)))
    return 0


def run_ask(ctx: AppContext, args: argparse.Namespace) -> int:
    return print_answer(asyncio.run(ask(ctx, args.question, args.refines, profile=args.model)))


def run_rerun(ctx: AppContext, args: argparse.Namespace) -> int:
    return print_answer(asyncio.run(answer(ctx, args.question_id, profile=args.model)))


def run_questions(ctx: AppContext, args: argparse.Namespace) -> int:
    lines = questions(ctx)
    if not lines:
        print('no questions yet: oracle discover ask "which European companies gain from grid batteries?"')
    for line in lines:
        q, last = line.question, line.last
        refines = f" (follow-up to {q.refines})" if q.refines else ""
        outcome = f", last {last.status} {last.started_at[:10]}: {len(last.shortlist)} shortlisted" if last else ""
        print(f"{q.id:>4}  {q.text}{refines}{' [auto]' if q.auto else ''}")
        print(f"      {line.answers} answer(s){outcome}")
    return 0


def run_show(ctx: AppContext, args: argparse.Namespace) -> int:
    v = question(ctx, args.question_id)
    print(f"Question {v.question.id}: {v.asked}")
    for f in v.follow_ups:
        print(f"  follow-up {f.id}: {f.text}")
    if not v.answers:
        print(f"not answered yet: oracle discover rerun {v.question.id}")
    for a in v.answers:
        print()
        print_answer(a)
    return 0


def run_add(ctx: AppContext, args: argparse.Namespace) -> int:
    added = asyncio.run(add_to_discovered(ctx, args.answer_id, args.rank))
    print(f"added {added.symbol}  {added.name} to the discovered list ({added.why})")
    return 0


def run_auto(ctx: AppContext, args: argparse.Namespace) -> int:
    q = set_auto(ctx, args.question_id, args.switch == "on")
    if q.auto:
        print(f"question {q.id} is on auto: each answer adds what passes validation to the discovered list "
              f"(rerun it now: oracle discover rerun {q.id}; the midday pass reruns it too)")
    else:
        print(f"question {q.id} is off auto: nothing is added without you")
    return 0


def print_answer(v: AnswerView) -> int:
    a = v.answer
    print(f"Answer {a.id} to question {a.question_id}, {a.started_at} ({a.model}): {a.status}")
    if a.error:
        print(f"  failed: {a.error}")
    if a.searches:
        _wrapped(f"Searched {'; '.join(a.searches)}", "  ")
    against = f" (new marks what answer {v.compared_to} did not have)" if v.compared_to else ""
    if v.shortlist:
        print(f"  Shortlist{against}:")
    for line in v.shortlist:
        i = line.item
        marks = "".join(f" [{m}]" for m, on in (("new", line.new), ("watched", line.watched),
                                                  ("discovered", line.discovered_as is not None)) if on)
        print(f"  {i.n:>2}. {i.symbol}  {i.company}{marks}")
        _wrapped(i.why, "        ")
        if i.validation:
            _wrapped(f"auto: {validation_line(i.validation)}", "        ")
        for e in line.evidence:
            print(f"        - {(e.published_at or 'undated')[:10]} {e.outlet or 'unknown outlet'}: {e.title}")
            print(f"          {e.url}")
    for i in v.skipped:
        print(f"  left off {i.name}: {i.skipped}")
    for i in v.gone:
        print(f"  gone since answer {v.compared_to}: {i.symbol} {i.company}")
    for note in a.notes:
        print(f"  note: {note}")
    if v.shortlist:
        print(f"  Watch: oracle watch add SYMBOL; research: oracle research SYMBOL; "
              f"discovered list: oracle discover add {a.id} RANK")
    return 1 if a.status == "failed" else 0


def validation_line(v: Validation) -> str:
    """What an auto question's validation made of a company, in one line."""
    if v.added:
        return "added to the discovered list, every check passed"
    if failed := v.failed:
        return f"stays a suggestion: failed {failed.name} ({failed.detail})"
    return f"stays a suggestion: {v.note}"


def print_run(r: DiscoveryRun) -> None:
    if r.note:
        print(f"Discovery: {r.note}")
        return
    print(f"Discovery from {', '.join(r.seeds)}: added {len(r.added)}")
    for a in r.added:
        print(f"  added {a.symbol}  {a.name}  ({a.why})")
    for s in r.skipped:
        print(f"  skipped {s.name}: {s.reason}")
    for e in r.source_errors:
        print(f"  could not use {e}")


def _wrapped(text: str, indent: str) -> None:
    print(textwrap.fill(text, WIDTH, initial_indent=indent, subsequent_indent=indent + "  "))

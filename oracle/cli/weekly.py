"""`oracle lab page` and `oracle lab apply-marks`: the weekly page (research-lab 08) and the marks read back
from its database."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from oracle.services.context import AppContext
from oracle.services.errors import InvalidQuery
from oracle.services.weekly_page_service import CAPABILITIES, COLLECTION, PAGE_PATH, apply_marks, make_page


def add_commands(sub) -> None:
    p = sub.add_parser("page", description="the weekly page: results table, deaths by stage, the next candidates "
                                           "with a recommendation and go / drop / dig-deeper buttons, papers I'd "
                                           "like and open questions, as one HTML file to publish as a claude.ai "
                                           "artifact")
    p.add_argument("--out", type=Path, help=f"where to write it (default {PAGE_PATH.as_posix()} beside oracle.toml)")
    p.add_argument("--question", action="append", default=[], metavar="TEXT",
                   help="an open question for the user (repeatable)")
    p.set_defaults(handler=run_page)

    p = sub.add_parser("apply-marks", description="apply the page's marks to the literature index with who and when: "
                                                  f"a JSON list of the {COLLECTION} documents, each {{id, mark, "
                                                  "note, by, at, mark_at}}; a mark already applied at its mark_at is "
                                                  "skipped, unknown methods are reported")
    p.add_argument("file", type=Path)
    p.set_defaults(handler=run_apply_marks)


def run_page(ctx: AppContext, args: argparse.Namespace) -> int:
    made = make_page(ctx, args.question, args.out)
    print(f"Wrote {made.path}: {made.rows} rows, {len(made.candidates)} candidates "
          f"({', '.join(made.candidates) or 'none'}), {made.papers} papers wanted, {made.questions} open questions.")
    print(f"Publish it with capabilities {json.dumps(CAPABILITIES)}")
    return 0


def run_apply_marks(ctx: AppContext, args: argparse.Namespace) -> int:
    try:
        docs = json.loads(args.file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise InvalidQuery(f"cannot read {args.file}: {exc}") from exc
    results = apply_marks(ctx, docs)
    for r in results:
        print(f"{r.id:<9}{r.outcome:<12}{r.detail}")
    counts = Counter(r.outcome for r in results)
    print(f"{len(results)} marks: " + (", ".join(f"{n} {o}" for o, n in counts.items()) or "none"))
    return 0

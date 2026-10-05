"""`oracle lab`'s full-text commands (research-lab 07): fetch an open-access copy, the papers I'd like list,
and the inbox scan."""

from __future__ import annotations

import argparse
import asyncio

from oracle.services.context import AppContext
from oracle.services.papers_service import fetch, scan_inbox, text, texts, unwant, want, wanted

_BY = "who does it (default user; the autopilot passes autopilot)"
_SOURCE = "the source: DOI, SSRN, arXiv or NBER id, its URL, or its number (#12)"


def add_commands(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("fetch", description="look up a legal open-access copy of a recorded source (Unpaywall, then "
                                            "Semantic Scholar) and keep its text; none found puts it on papers I'd "
                                            "like. A few API calls: fine any time")
    p.add_argument("source", help=_SOURCE)
    p.add_argument("--by", default="user", help=_BY)
    p.set_defaults(handler=run_fetch)

    p = sub.add_parser("wanted", description="papers I'd like: sources with no open copy, to fetch through a library "
                                             "and drop in the inbox folder")
    p.set_defaults(handler=run_wanted)

    p = sub.add_parser("want", description="put a recorded source on papers I'd like")
    p.add_argument("source", help=_SOURCE)
    p.add_argument("--note", default="")
    p.add_argument("--by", default="user", help=_BY)
    p.set_defaults(handler=run_want)

    p = sub.add_parser("unwant", description="take a source off papers I'd like")
    p.add_argument("source", help=_SOURCE)
    p.set_defaults(handler=run_unwant)

    p = sub.add_parser("text", description="the sources with a full text (where from, when, pages); with a source, "
                                           "its kept text")
    p.add_argument("source", nargs="?", help=_SOURCE)
    p.set_defaults(handler=run_text)

    p = sub.add_parser("inbox", description="read the PDFs dropped in the inbox folder ([research] inbox), match "
                                            "each to its source by DOI or title, keep its text and move it to done/")
    p.add_argument("--by", default="user", help=_BY)
    p.set_defaults(handler=run_inbox)


def _name(s) -> str:
    return f"#{s.id} {s.title}" + (f" ({', '.join(s.keys)})" if s.keys else "")


def run_fetch(ctx: AppContext, args: argparse.Namespace) -> int:
    r = asyncio.run(fetch(ctx, args.source, args.by))
    print(_name(r.source))
    if r.copy:
        print(f"  full text: {r.pages} pages, {r.chars} characters from {r.copy.via}"
              + (f" ({r.copy.licence})" if r.copy.licence else ""))
        print(f"  {r.copy.url}")
    elif r.wanted:
        print("  no legal open copy could be read: added to papers I'd like "
              "(drop the PDF in the inbox when you have it)")
    print(f"  {r.note}")
    return 0


def run_wanted(ctx: AppContext, args: argparse.Namespace) -> int:
    rows = wanted(ctx)
    for w in rows:
        print(_name(w.source))
        print(f"  {w.at[:10]} by {w.by}: {w.reason}" + (f"; note: {w.note}" if w.note else ""))
    print(f"{len(rows)} paper{'' if len(rows) == 1 else 's'} wanted")
    return 0


def run_want(ctx: AppContext, args: argparse.Namespace) -> int:
    w = want(ctx, args.source, args.note, args.by)
    print(f"Wanted: {_name(w.source)}")
    return 0


def run_unwant(ctx: AppContext, args: argparse.Namespace) -> int:
    unwant(ctx, args.source)
    print(f"Off the list: {args.source}")
    return 0


def _origin(f) -> str:
    return f"from {f.via}, {f.at[:10]} by {f.by}, {f.pages} pages" + (f", {f.licence}" if f.licence else "")


def run_text(ctx: AppContext, args: argparse.Namespace) -> int:
    if args.source is None:
        rows = texts(ctx)
        for h in rows:
            print(_name(h.source))
            print(f"  {_origin(h.full_text)}")
        print(f"{len(rows)} full text{'' if len(rows) == 1 else 's'}")
        return 0
    kept = text(ctx, args.source)
    print(_name(kept.source))
    print(f"  {_origin(kept.full_text)}")
    if kept.full_text.url:
        print(f"  {kept.full_text.url}")
    print()
    print(kept.text)
    return 0


def run_inbox(ctx: AppContext, args: argparse.Namespace) -> int:
    scan = scan_inbox(ctx, args.by)
    print(f"Inbox {scan.folder}:")
    for r in scan.results:
        print(f"  {r.file}: " + (f"{_name(r.source)} by {r.matched}, {r.pages} pages; {r.note}" if r.source
                                  else r.note))
    if not scan.results:
        print("  no PDFs waiting")
    return 0

"""`oracle lab`'s literature index commands: lookup, record, search-log, mark, methods, method."""

from __future__ import annotations

import argparse
import textwrap

from oracle.services.context import AppContext
from oracle.services.literature_service import (
    MARKS, QUALITIES, STATUSES, Method, MethodHit, Source, log_search, lookup, mark, method, methods, record, searches,
)

_BY = "who does it (default user; the autopilot passes autopilot)"


def add_commands(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("lookup", description="find a source by id (DOI, SSRN, arXiv, NBER, URL) or a paper or method "
                                             "by title, name or ref (fuzzy); prints the 5 nearest methods with score "
                                             "and status")
    p.add_argument("query")
    p.set_defaults(handler=run_lookup)

    p = sub.add_parser("record", description="record a source (new, or update one found by any of its ids or its "
                                             "title), link it to methods it yields, and optionally add a new method")
    p.add_argument("id", nargs="?", help="DOI, SSRN or arXiv id, NBER paper, or URL")
    p.add_argument("--title")
    p.add_argument("--authors")
    p.add_argument("--year", type=int)
    p.add_argument("--quality", choices=QUALITIES, help="a new source without one is abstract-only")
    p.add_argument("--read", dest="read_on", metavar="YYYY-MM-DD", help="the day it was read")
    p.add_argument("--note")
    p.add_argument("--method", action="append", default=[], metavar="REF", help="a method it yields (repeatable)")
    p.add_argument("--new-method", metavar="NAME", help="add a method found in it (status new)")
    p.add_argument("--family", default="", help="the new method's family")
    p.add_argument("--definition", default="", help="the new method's mechanical rule")
    p.add_argument("--alias", action="append", default=[], help="another name for the new method (repeatable)")
    p.add_argument("--by", default="user", help=_BY)
    p.set_defaults(handler=run_record)

    p = sub.add_parser("search-log", description="log a literature search (query, where, what it found); without a "
                                                 "query, list the recent ones")
    p.add_argument("query", nargs="?")
    p.add_argument("--where", default="", help="where it ran, e.g. SSRN, Google Scholar, OSAP")
    p.add_argument("--found", action="append", default=[], metavar="ID",
                   help="a recorded source it turned up, by id or URL (repeatable)")
    p.add_argument("--note", default="")
    p.add_argument("--by", default="user", help=_BY)
    p.set_defaults(handler=run_search_log)

    p = sub.add_parser("mark", description="go, drop or dig (deeper: back to the literature stage); keeps who and when")
    p.add_argument("ref")
    p.add_argument("how", choices=sorted(MARKS))
    p.add_argument("--note", default="")
    p.add_argument("--by", default="user", help=_BY)
    p.set_defaults(handler=run_mark)

    p = sub.add_parser("methods", description="every method in the index, with its funnel status")
    p.add_argument("--status", choices=STATUSES)
    p.set_defaults(handler=run_methods)

    p = sub.add_parser("method", description="one method: its rule, evidence, sources and mark history")
    p.add_argument("ref")
    p.set_defaults(handler=run_method)


def _wrap(text: str, indent: str = "  ") -> None:
    print(textwrap.fill(text, 100, initial_indent=indent, subsequent_indent=indent))


def _cite(s: Source) -> str:
    who = " ".join(x for x in (s.authors, str(s.year) if s.year else "") if x)
    return (f"{who + ', ' if who else ''}{s.title}{f' (also titled {s.also_titled})' if s.also_titled else ''} "
            f"({s.quality}{', read ' + s.read_on if s.read_on else ''})")


def print_source(s: Source, score: float | None = None) -> None:
    print(f"  {f'[{score:.2f}] ' if score is not None else ''}{_cite(s)}")
    print(f"    {', '.join(s.keys) or 'no id'}{'  ' + s.url if s.url else ''}")
    if s.methods:
        print(f"    methods: {', '.join(s.methods)}")


def print_hits(hits: list[MethodHit]) -> None:
    print("Nearest methods:")
    for h in hits:
        print(f"  {h.ref:<6} {h.status:<12} {h.score:.2f}  {h.name}  "
              f"({h.matched}{'; rating ' + str(h.rating) if h.rating is not None else ''})")
    if not hits:
        print("  none")


def run_lookup(ctx: AppContext, args: argparse.Namespace) -> int:
    found = lookup(ctx, args.query)
    print(f"Lookup {found.query!r}: {found.note}" + (f" ({found.key})" if found.key else ""))
    if found.sources:
        print("Sources:")
        for h in found.sources:
            print_source(h.source, h.score)
    print_hits(found.methods)
    return 0


def run_record(ctx: AppContext, args: argparse.Namespace) -> int:
    r = record(ctx, args.id, title=args.title, authors=args.authors, year=args.year, quality=args.quality,
               note=args.note, read_on=args.read_on, methods=args.method, new_method=args.new_method,
               family=args.family, definition=args.definition, aliases=args.alias, by=args.by)
    if r.source:
        print(f"{'New source' if r.created else 'Updated source'} #{r.source.id}:")
        print_source(r.source)
    if r.method:
        print(f"New method {r.method.ref}: {r.method.name} ({r.method.status})")
    if r.nearest:
        print_hits(r.nearest)
    return 0


def run_search_log(ctx: AppContext, args: argparse.Namespace) -> int:
    if args.query is None:
        rows = searches(ctx)
        for s in rows:
            print(f"{s.at[:16].replace('T', ' ')} {s.place} {s.query!r} by {s.by}" + (f": {s.note}" if s.note else ""))
            for f in s.found:
                print(f"  found: {_cite(f)}")
        if not rows:
            print("No searches logged yet.")
        return 0
    s = log_search(ctx, args.query, args.where, args.found, args.note, args.by)
    print(f"Logged search #{s.id}: {s.query!r} in {s.place}, {len(s.found)} source(s) found.")
    return 0


def run_mark(ctx: AppContext, args: argparse.Namespace) -> int:
    m = mark(ctx, args.ref, args.how, args.by, args.note)
    print(f"{m.ref} {m.status} (marked {args.how} by {m.status_by} at {m.status_at[:16].replace('T', ' ')} UTC)")
    return 0


def _line(m: Method) -> str:
    rating = "-" if m.rating is None else str(m.rating)
    return f"{m.ref:<6} {m.status:<12} {rating:>2}  {m.name}" + (f"  [test {m.test_key}]" if m.test_key else "")


def run_methods(ctx: AppContext, args: argparse.Namespace) -> int:
    rows = methods(ctx, args.status)
    for m in rows:
        print(_line(m))
    print(f"{len(rows)} method{'' if len(rows) == 1 else 's'}")
    return 0


def run_method(ctx: AppContext, args: argparse.Namespace) -> int:
    m = method(ctx, args.ref)
    print(f"{m.ref} {m.name}")
    print(f"  {m.family}; {m.origin}; status {m.status} (by {m.status_by}, {m.status_at[:16].replace('T', ' ')} UTC)"
          + (f"; rating {m.rating}" if m.rating is not None else "")
          + (f"; archive: {m.archive_status}" if m.archive_status else "")
          + (f"; duplicate of {m.duplicate_of}" if m.duplicate_of else ""))
    for label, text in (("rule", m.definition), ("evidence", m.evidence), ("why", m.why), ("result", m.result),
                        ("archive", m.archive_note if m.archive_note != m.archive_status else "")):
        if text:
            _wrap(f"{label}: {text}")
    if m.aliases:
        print(f"  aliases: {', '.join(m.aliases)}")
    if m.see_also:
        print(f"  see also: {', '.join(m.see_also)}")
    if m.test_key:
        print(f"  test: {m.test_key}")
    print("Sources:" if m.sources else "No sources.")
    for s in m.sources:
        print_source(s)
    if m.marks:
        print("Marks:")
        for k in m.marks:
            print(f"  {k.at[:16].replace('T', ' ')} {k.mark}: {k.status_from} -> {k.status_to} by {k.by}"
                  + (f": {k.note}" if k.note else ""))
    return 0

from __future__ import annotations

import argparse
import asyncio

from oracle.services.context import AppContext
from oracle.services.edges_service import company_edges, confirm_edge, dismiss_edge, find_edges, find_list_edges

UNCALIBRATED = "Laya's probabilities are uncalibrated"


def register(parser: argparse.ArgumentParser) -> None:
    parser.description = ("supplier, customer, competitor and partner ties read from stored headlines and US 10-Ks, "
                          "typed by a keyword rule and Laya; linked where both agree, else left for you to confirm")
    sub = parser.add_subparsers(dest="edges_command", required=True)
    find = sub.add_parser("find", help="read a company's (or a list's) documents not yet read for ties")
    find.add_argument("symbol", nargs="?")
    find.add_argument("--list", dest="watchlist", help="every company on this list instead (main is the main list)")
    find.set_defaults(handler=find_cmd)
    show = sub.add_parser("show", help="the company's ties, linked and unconfirmed, with their evidence")
    show.add_argument("symbol")
    show.set_defaults(handler=show_cmd)
    for name, text, handler in (("confirm", "make an unconfirmed tie a link", confirm_cmd),
                                ("delete", "drop an unconfirmed tie", delete_cmd)):
        p = sub.add_parser(name, help=text)
        p.add_argument("symbol")
        p.add_argument("edge_id", type=int)
        p.set_defaults(handler=handler)


def find_cmd(ctx: AppContext, args: argparse.Namespace) -> int:
    if (args.symbol is None) == (args.watchlist is None):
        print("give a symbol or --list, not both")
        return 2
    say = lambda line: print(line, flush=True)  # noqa: E731
    run = asyncio.run(find_list_edges(ctx, args.watchlist, on_progress=say) if args.watchlist
                      else find_edges(ctx, [args.symbol], on_progress=say))
    print(f"{len(run.symbols)} companies: {run.documents} documents read ({run.filings} 10-Ks), {run.candidates} ties, "
          f"{run.linked} linked, {run.unconfirmed} unconfirmed ({run.seconds:.1f} s"
          + (f", {run.device})" if run.device else ")"))
    for e in run.errors:
        print(f"  {e}")
    return 0


def show_cmd(ctx: AppContext, args: argparse.Namespace) -> int:
    edges = company_edges(ctx, args.symbol)
    if not edges:
        print(f"no ties read for {args.symbol}; run `oracle edges find {args.symbol}`")
        return 0
    print(f"{len(edges)} ties; {UNCALIBRATED}")
    for e in edges:
        print(f"[{e.id}] {e.state:<11} {e.other} ({e.other_name or '?'}) is a {e.relation} "
              f"(rule {e.rule}, Laya {e.choice} {e.prob:.2f}), {e.kind} {e.known_at[:10]}: {e.evidence}")
    return 0


def confirm_cmd(ctx: AppContext, args: argparse.Namespace) -> int:
    e = confirm_edge(ctx, args.symbol, args.edge_id)
    print(f"{e.other} is now linked as a {e.relation} of {e.symbol}, first seen {e.known_at[:10]}")
    return 0


def delete_cmd(ctx: AppContext, args: argparse.Namespace) -> int:
    e = dismiss_edge(ctx, args.symbol, args.edge_id)
    print(f"dropped tie {e.id}: {e.other} as a {e.relation} of {e.symbol}")
    return 0

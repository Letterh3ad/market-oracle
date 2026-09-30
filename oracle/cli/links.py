from __future__ import annotations

import argparse

from oracle.services.context import AppContext
from oracle.services.links_service import USER_RELATIONS, add_link, company_links, delete_link, relation_words


def register(parser: argparse.ArgumentParser) -> None:
    parser.description = "companies linked to a company in the news (or the same industry), kept by discovery"
    sub = parser.add_subparsers(dest="links_command", required=True)
    show = sub.add_parser("show", help="a company's links, strongest first, with the headlines behind them")
    show.add_argument("symbol")
    show.set_defaults(handler=show_cmd)
    add = sub.add_parser("add", help="your own link: OTHER is RELATION of SYMBOL (dated today)")
    add.add_argument("symbol")
    add.add_argument("other")
    add.add_argument("relation", help=", ".join(USER_RELATIONS))
    add.set_defaults(handler=add_cmd)
    delete = sub.add_parser("delete", help="delete a wrong link; it is never recorded again")
    delete.add_argument("symbol")
    delete.add_argument("id", type=int, help="the link's number, as `links show` prints it")
    delete.set_defaults(handler=delete_cmd)


def show_cmd(ctx: AppContext, args: argparse.Namespace) -> int:
    links = company_links(ctx, args.symbol)
    if not links:
        print(f"no links for {args.symbol}")
    for x in links:
        print(f"#{x.id}  {x.symbol}  {x.name or ''}: {x.why} (found by {x.source}, last seen {x.last_seen[:10]})")
        for e in x.evidence:
            print(f"      {(e.published_at or 'undated')[:10]} {e.outlet or 'unknown outlet'}: {e.title}")
    return 0


def add_cmd(ctx: AppContext, args: argparse.Namespace) -> int:
    x = add_link(ctx, args.symbol, args.other, args.relation)
    print(f"{x.symbol} ({x.name or x.symbol}) is now {relation_words(args.relation)} of {args.symbol}")
    return 0


def delete_cmd(ctx: AppContext, args: argparse.Namespace) -> int:
    x = delete_link(ctx, args.symbol, args.id)
    print(f"deleted the link to {x.symbol} ({x.name or x.symbol}); it is never recorded again")
    return 0

from __future__ import annotations

import argparse

from oracle.services.context import AppContext
from oracle.services.tags_service import add_tag, list_tags, remove_tag, tags_of


def register(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="tag_command", required=True)
    add = sub.add_parser("add", help="tag a company (through any of its listings)")
    add.add_argument("symbol")
    add.add_argument("tag", help="quote it if it has spaces")
    add.set_defaults(handler=add_cmd)
    remove = sub.add_parser("remove", help="untag a company")
    remove.add_argument("symbol")
    remove.add_argument("tag")
    remove.set_defaults(handler=remove_cmd)
    show = sub.add_parser("list", help="every tag, or one company's tags")
    show.add_argument("symbol", nargs="?")
    show.set_defaults(handler=list_cmd)


def add_cmd(ctx: AppContext, args: argparse.Namespace) -> int:
    t = add_tag(ctx, args.symbol, args.tag)
    print(f"tagged {t.name} ({t.symbol}) with '{t.tag}'")
    return 0


def remove_cmd(ctx: AppContext, args: argparse.Namespace) -> int:
    if remove_tag(ctx, args.symbol, args.tag):
        print(f"removed '{args.tag}' from {args.symbol}")
        return 0
    print(f"{args.symbol} was not tagged '{args.tag}'")
    return 1


def list_cmd(ctx: AppContext, args: argparse.Namespace) -> int:
    if args.symbol:
        tags = tags_of(ctx, args.symbol)
        print("\n".join(tags) if tags else "no tags")
        return 0
    summaries = list_tags(ctx)
    if not summaries:
        print("no tags")
        return 0
    width = max(len(s.tag) for s in summaries) + 2
    for s in summaries:
        orphaned = [f"{o} (no longer listed)" for o in s.orphaned]
        print(f"{s.tag:<{width}}{'  '.join([*s.symbols, *orphaned])}")
    return 0

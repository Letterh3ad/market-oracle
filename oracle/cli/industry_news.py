from __future__ import annotations

import argparse
import asyncio

from oracle.services.context import AppContext
from oracle.services.industry_research_service import (
    BUTTON_FRESH_H, FRESH_H, industry_news, live_model_work, research_industry,
)


def register(parser: argparse.ArgumentParser) -> None:
    parser.description = ("daily industry research: new headlines of a company and its industry map, scored with Laya "
                          "per related pair (own, competitor, supplier, customer, industry) into daily features")
    sub = parser.add_subparsers(dest="industry_news_command", required=True)
    run = sub.add_parser("run", help="search and score now: one company and its map, or the tick's daily set")
    run.add_argument("symbol", nargs="?", help="omit for held, watched and main-list companies, as the tick does")
    run.set_defaults(handler=run_cmd)
    show = sub.add_parser("show", help="a company's features and each related company's latest scored events")
    show.add_argument("symbol")
    show.add_argument("--days", type=int, default=7, choices=range(0, 366), metavar="0-365",
                      help="days of features before today (default 7)")
    show.set_defaults(handler=show_cmd)


def run_cmd(ctx: AppContext, args: argparse.Namespace) -> int:
    say = lambda line: print(line, flush=True)  # noqa: E731
    r = asyncio.run(research_industry(ctx, [args.symbol] if args.symbol else None,
                                      fresh_h=BUTTON_FRESH_H if args.symbol else FRESH_H,
                                      busy=lambda: live_model_work(ctx), on_progress=say))
    print(r.summary)
    print(f"  searching {r.search_seconds:.0f} s, scoring {r.score_seconds:.0f} s; {r.found} found, "
          f"{r.undated} undated and {r.outside} older dropped, {r.already} pairs already scored")
    for f in r.failures[1:]:
        print(f"  {f}")
    return 0


def show_cmd(ctx: AppContext, args: argparse.Namespace) -> int:
    v = industry_news(ctx, args.symbol, days=args.days)
    print(f"{v.symbol} on {v.day} ({v.model}); last searched {v.last_searched or 'never'}. {v.caveat}")
    print("Features (day, relation: articles, direction x prob sum, events):")
    for f in v.recent or ():
        events = ", ".join(f"{k} {n}" for k, n in f.events.items())
        print(f"  {f.day} {f.relation:<10} {f.articles:>3}  {f.score:+.2f}  {events}")
    if not v.recent:
        print("  none stored")
    for c in (v.own, *v.related):
        print(f"{c.symbol} ({c.relation}): {c.day_articles} today, moved {c.day_score:+.2f}")
        for e in c.events:
            print(f"    {e.known_at[:10]} {e.direction} {e.direction_prob:.2f} {e.event}: {e.title}")
    return 0

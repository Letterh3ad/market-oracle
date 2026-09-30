from __future__ import annotations

import argparse

from oracle.services.context import AppContext
from oracle.services.laya_service import laya_scores, score_company

UNCALIBRATED = "probabilities are uncalibrated"


def register(parser: argparse.ArgumentParser) -> None:
    parser.description = "Laya, a small local model that scores each stored headline for a company"
    sub = parser.add_subparsers(dest="laya_command", required=True)
    score = sub.add_parser("score", help="score the company's stored articles not yet scored by this model")
    score.add_argument("symbol")
    score.set_defaults(handler=score_cmd)
    show = sub.add_parser("show", help="the company's scored articles, newest first")
    show.add_argument("symbol")
    show.set_defaults(handler=show_cmd)


def score_cmd(ctx: AppContext, args: argparse.Namespace) -> int:
    run = score_company(ctx, args.symbol, on_progress=lambda line: print(line, flush=True))
    where = f" on {run.device}" if run.device else ""
    print(f"{run.symbol}: {run.scored} scored{where}, {run.already} already scored, of {run.articles} articles "
          f"({run.seconds:.1f} s, {run.model})")
    return 0


def show_cmd(ctx: AppContext, args: argparse.Namespace) -> int:
    scores = laya_scores(ctx, args.symbol)
    if not scores:
        print(f"no Laya scores for {args.symbol}; run `oracle laya score {args.symbol}`")
        return 0
    print(f"{len(scores)} articles; {UNCALIBRATED}")
    for s in scores:
        day = s.known_at[:10] + ("" if s.dated else " (fetched)")
        print(f"{day}  relevant {s.relevant:.2f}  {s.direction:<4} {s.direction_probs[s.direction]:.2f}  "
              f"{s.event} {s.event_prob:.2f}  {s.title}")
    return 0

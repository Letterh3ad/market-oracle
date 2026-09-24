"""`oracle replay probe` and `oracle replay cutoff`: the model's measured cutoff."""

from __future__ import annotations

import argparse
import asyncio
import sys

from oracle.services.context import AppContext
from oracle.services.cutoff_service import CutoffProbe, no_replay_room, probe_cutoff, probe_profile, stored_probe

MODEL_HELP = "the model profile to probe (default: the research slot's)"


def add_commands(sub: argparse._SubParsersAction) -> None:
    probe = sub.add_parser("probe", help="ask the model dated questions to measure what it knows; replays start after")
    probe.add_argument("--model", metavar="PROFILE", default=None, help=MODEL_HELP)
    probe.set_defaults(handler=_probe)
    show = sub.add_parser("cutoff", help="the stored measured cutoff, with every question, reply and mark")
    show.add_argument("--model", metavar="PROFILE", default=None, help=MODEL_HELP)
    show.set_defaults(handler=_cutoff)


def _probe(ctx: AppContext, args: argparse.Namespace) -> int:
    probe = asyncio.run(probe_cutoff(ctx, args.model, on_progress=lambda line: print(line, file=sys.stderr, flush=True)))
    _print(ctx, probe)
    return 0


def _cutoff(ctx: AppContext, args: argparse.Namespace) -> int:
    name = probe_profile(ctx, args.model)
    probe = stored_probe(ctx, name)
    if probe is None:
        print(f"The model of profile {name} has not been probed yet: run `oracle replay probe`.")
        return 0
    _print(ctx, probe)
    print()
    for a in probe.answers:
        print(f"{'right' if a.right else 'wrong'}  {a.day}  {a.question}")
        print(f"       expected: {a.expected}; said: {a.reply}")
    return 0


def _print(ctx: AppContext, p: CutoffProbe) -> None:
    print(f"{p.provider} {p.model} (profile {p.profile}), question set {p.question_set}, "
          f"probed {p.probed_at:%Y-%m-%d %H:%M} UTC")
    for s in p.months:
        print(f"  {s.month}  {s.right:>3}/{s.total}  {'known' if s.known else ''}".rstrip())
    print(f"Measured cutoff: {p.cutoff} (last month known {p.last_known or 'none'}; claimed {p.claimed}).")
    if note := no_replay_room(ctx, p):
        print(note)
    else:
        print(f"Replays start after it, from {p.replay_from}.")

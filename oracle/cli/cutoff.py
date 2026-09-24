"""`oracle replay probe` and `oracle replay cutoff`: the measured cutoff of the models a replay runs on."""

from __future__ import annotations

import argparse
import asyncio
import sys

from oracle.services.context import AppContext
from oracle.services.cutoff_service import CutoffProbe, ReplayCutoff, probe_replay_models, replay_cutoff

MODEL_HELP = "one profile to run every stage on (default: each stage's [models.slots] profile)"


def add_commands(sub: argparse._SubParsersAction) -> None:
    probe = sub.add_parser("probe", help="ask dated questions to measure what the replay's models know; with no "
                                         "--model, each stage model not probed yet (all again when every one is)")
    probe.add_argument("--model", metavar="PROFILE", default=None, help=MODEL_HELP)
    probe.set_defaults(handler=_probe)
    show = sub.add_parser("cutoff", help="the stored cutoffs, with every question, reply and mark")
    show.add_argument("--model", metavar="PROFILE", default=None, help=MODEL_HELP)
    show.set_defaults(handler=_cutoff)


def _progress(line: str) -> None:
    print(line, file=sys.stderr, flush=True)


def _probe(ctx: AppContext, args: argparse.Namespace) -> int:
    for probe in asyncio.run(probe_replay_models(ctx, args.model, on_progress=_progress)):
        _print_probe(probe)
    _print_cutoff(replay_cutoff(ctx, args.model))
    return 0


def _cutoff(ctx: AppContext, args: argparse.Namespace) -> int:
    rc = replay_cutoff(ctx, args.model)
    for name, probe in rc.probes.items():
        if probe is None:
            print(f"{name}: not probed yet")
            continue
        _print_probe(probe)
        for a in probe.answers:
            print(f"  {'right' if a.right else 'wrong'}  {a.day}  {a.question}")
            print(f"         expected: {a.expected}; said: {a.reply}")
    _print_cutoff(rc)
    return 0


def _print_probe(p: CutoffProbe) -> None:
    print(f"{p.profile}: {p.provider} {p.model}, question set {p.question_set}, "
          f"probed {p.probed_at:%Y-%m-%d %H:%M} UTC")
    for s in p.months:
        print(f"  {s.month}  {s.right:>3}/{s.total}  {'known' if s.known else ''}".rstrip())
    print(f"  Cutoff: {p.cutoff} (last month known {p.last_known or 'none'}; claimed {p.claimed}).")


def _print_cutoff(rc: ReplayCutoff) -> None:
    print("Stages: " + ", ".join(f"{stage} on {name}" for stage, name in rc.stages.items()))
    if rc.cutoff:
        print(f"Measured cutoff: {rc.cutoff} (set by {rc.set_by}).")
    if rc.note:
        print(rc.note)
    elif rc.replay_from:
        print(f"Replays start after it, from {rc.replay_from}.")

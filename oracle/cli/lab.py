from __future__ import annotations

import argparse
import json
from dataclasses import fields
from pathlib import Path

from oracle.cli import literature
from oracle.services.context import AppContext
from oracle.services.errors import InvalidQuery
from oracle.services.lab_registry import Template, commit_test, drop_test, register_test, registration
from oracle.services.lab_service import Scoreboard, bar_text, scoreboard

TEMPLATE_FIELDS = tuple(f.name for f in fields(Template))


def register(parser: argparse.ArgumentParser) -> None:
    parser.description = ("the research lab: every pre-registered test, its PASS bar and verdict (research-lab spec 6, "
                          "8), and the literature index of methods, sources and searches; kept in the research "
                          "database")
    sub = parser.add_subparsers(dest="lab_command", required=True)
    sub.add_parser("scoreboard", description="every registered test, one row per horizon: confirm IC or excess, t, "
                                             "the bar it was judged by, verdict and power").set_defaults(
        handler=run_scoreboard)
    show = sub.add_parser("show", description="one test's full pre-registration")
    show.add_argument("key")
    show.set_defaults(handler=run_show)
    reg = sub.add_parser("register", description="record a pre-registration from a JSON file of the template's "
                                                 f"fields ({', '.join(TEMPLATE_FIELDS)}); its bar is frozen now")
    reg.add_argument("file", type=Path)
    reg.set_defaults(handler=run_register)
    commit = sub.add_parser("commit", description="record the commit that put the registration in DECISIONS; data "
                                                  "runs are refused until then")
    commit.add_argument("key")
    commit.add_argument("hash")
    commit.set_defaults(handler=run_commit)
    drop = sub.add_parser("drop", description="remove a registration the gate turned down (only before its commit)")
    drop.add_argument("key")
    drop.set_defaults(handler=run_drop)
    literature.add_commands(sub)


def _num(x: float | None, fmt: str) -> str:
    return "-" if x is None else format(x, fmt)


def print_scoreboard(b: Scoreboard) -> None:
    print(f"{b.n} hypotheses registered (each horizon once); a one-horizon test registered now needs t >= "
          f"{b.next_bar:.2f}")
    print(f"{'test':<24}{'type':<9}{'h':>4}  {'measure':<23}{'value':>9}{'t':>7}  {'bar':<10}{'power':>8}  verdict")
    for r in b.rows:
        value = _num(r.value, "+.2%" if r.measure != "IC" else "+.4f")
        bar = r.bar if len(r.bar) <= 10 else "own rule"
        print(f"{r.key:<24}{r.kind:<9}{_num(r.horizon, 'd'):>4}  {r.measure:<23}{value:>9}{_num(r.t, '.2f'):>7}  "
              f"{bar:<10}{_num(r.power, '.4f'):>8}  {r.verdict}")
    if rules := {r.key: r.bar for r in b.rows if len(r.bar) > 10}:
        print()
        for key, rule in rules.items():
            print(f"{key}: {rule}")
    print("Tests registered before the lab keep the bar they were judged by; power is the smallest IC the confirm "
          "window could see at that bar.")


def run_scoreboard(ctx: AppContext, args: argparse.Namespace) -> int:
    print_scoreboard(scoreboard(ctx))
    return 0


def run_show(ctx: AppContext, args: argparse.Namespace) -> int:
    r = registration(ctx, args.key)
    print(f"{r.key}: {r.label} ({r.kind}{', registered before the lab' if r.legacy else ''})")
    for name, value in (("registered", r.registered_on), ("horizons", ", ".join(map(str, r.horizons)) or "-"),
                        ("rule", r.rule), ("universe", r.universe),
                        ("expected", f"{r.expected_sign}, {r.expected_size} ({r.source})"), ("power", r.power),
                        ("costs", r.costs), ("bar", bar_text(r)), ("pass rule", r.bar_rule),
                        ("kill stage", r.kill_stage), ("parent", r.parent), ("near a FAIL", r.near_fail),
                        ("different because", r.different_because), ("commit", r.commit_hash or "not committed: no data run yet"), ("note", r.note)):
        if value:
            print(f"  {name}: {value}")
    return 0


def run_register(ctx: AppContext, args: argparse.Namespace) -> int:
    try:
        raw = json.loads(args.file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise InvalidQuery(f"cannot read {args.file}: {exc}") from exc
    if not isinstance(raw, dict) or (unknown := set(raw) - set(TEMPLATE_FIELDS)):
        raise InvalidQuery(f"a JSON object of {', '.join(TEMPLATE_FIELDS)}"
                           + (f"; not {', '.join(sorted(unknown))}" if isinstance(raw, dict) else ""))
    try:
        t = Template(**{**raw, "horizons": tuple(raw.get("horizons") or ())})
    except TypeError as exc:
        raise InvalidQuery(f"the template needs {', '.join(TEMPLATE_FIELDS[:12])}") from exc
    r = register_test(ctx, t)
    print(f"Registered {r.key} on {r.registered_on}: {r.bar_rule}")
    print("Commit the entry to DECISIONS (naming the key in backticks), then: oracle lab commit "
          f"{r.key} <hash>")
    return 0


def run_commit(ctx: AppContext, args: argparse.Namespace) -> int:
    r = commit_test(ctx, args.key, args.hash)
    print(f"{r.key} committed at {r.commit_hash[:7]}: its data runs may start")
    return 0


def run_drop(ctx: AppContext, args: argparse.Namespace) -> int:
    drop_test(ctx, args.key)
    print(f"Dropped {args.key}")
    return 0

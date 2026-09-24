from __future__ import annotations

import argparse
import asyncio

from oracle.services.context import AppContext
from oracle.services.models_service import model_profile_rows, try_model_profile


def register(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="models_command", required=True)
    sub.add_parser("list", help="each model profile; keys only as set or unset").set_defaults(handler=_list)
    test = sub.add_parser("test", help="send one tiny prompt and report the reply time or the error")
    test.add_argument("name")
    test.set_defaults(handler=_test)


def _list(ctx: AppContext, args: argparse.Namespace) -> int:
    print(f"{'name':<14}{'provider':<12}{'model':<32}{'context':>9}  key")
    for r in model_profile_rows(ctx):
        key = {"none": "-", "fallback": r["key_fallback"]}.get(r["key_state"], r["key_state"])
        if r["key_env"]:
            key += f" ({r['key_env']})"
        source = "  from [llm]" if r["from_llm"] else ""
        print(f"{r['name']:<14}{r['provider']:<12}{r['model']:<32}{r['context']:>9}  {key}{source}")
    return 0


def _test(ctx: AppContext, args: argparse.Namespace) -> int:
    result = asyncio.run(try_model_profile(ctx, args.name))
    if result.ok:
        print(f"{result.name}: ok in {result.seconds:.1f}s")
        return 0
    print(f"{result.name}: failed after {result.seconds:.1f}s: {result.error}")
    return 1

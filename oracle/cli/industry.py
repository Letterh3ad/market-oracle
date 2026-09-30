from __future__ import annotations

import argparse

from oracle.services.context import AppContext
from oracle.services.industry_service import company_activity, industry_map


def register(parser: argparse.ArgumentParser) -> None:
    parser.description = ("a company's industry map: competitors, peers and linked companies, how each is related "
                          "and since when, and what each is doing now (stored data only)")
    parser.add_argument("symbol")
    parser.set_defaults(handler=run)


def run(ctx: AppContext, args: argparse.Namespace) -> int:
    m = industry_map(ctx, args.symbol)
    print(f"Industry map of {m.symbol} ({m.name}): competitors by {m.basis or 'none'}")
    if m.note:
        print(f"  {m.note}")
    now = company_activity(ctx, [r.symbol for r in m.related])
    for r in m.related:
        how = "; ".join(f"{x.why} ({x.source}, " + (f"first known {x.first_known[:10]})" if x.first_known
                                                   else "static field)") for x in r.relations)
        print(f"{r.symbol}  {r.name or ''}  [{r.market_cap or 'size n/a'}]: {how}")
        a = now[r.symbol]
        print(f"    5d {_pct(a.move_5d)}  20d {_pct(a.move_20d)}  last earnings {a.last_earnings or 'n/a'}  "
              f"next earnings {a.next_earnings or 'n/a'}" + (f"  (closes to {a.last_close})" if a.last_close else ""))
        for e in dict.fromkeys((*r.evidence, *a.headlines)):
            print(f"    {(e.published_at or 'undated')[:10]} {e.outlet or 'unknown outlet'}: {e.title}")
    return 0


def _pct(v: float | None) -> str:
    return f"{v:+.1%}" if v is not None else "n/a"

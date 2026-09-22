from __future__ import annotations

import argparse
import asyncio
import textwrap

from oracle.cli.common import print_signal
from oracle.services.company_service import FIGURES, company_view
from oracle.services.context import AppContext
from oracle.services.fx_service import credits


def register(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("symbol")
    parser.set_defaults(handler=run)


def run(ctx: AppContext, args: argparse.Namespace) -> int:
    view = asyncio.run(company_view(ctx, args.symbol))
    p = view.profile
    base = ctx.config.base_currency
    print(f"{p.name} ({p.symbol})")
    place = ", ".join(x for x in (p.country, p.exchange, p.sector or p.category, p.industry or p.family) if x)
    others = f"; main listing {p.primary_symbol}" if p.primary_symbol != p.symbol else ""
    print(f"  {place}")
    print(f"  {p.listings} listing{'s' if p.listings != 1 else ''}{others}")
    if p.tags:
        print(f"  tags: {', '.join(p.tags)}")
    if p.website:
        print(f"  {p.website}")

    print()
    if view.price is None:
        print(f"  no price stored yet: run `oracle prices update {p.symbol}`")
    else:
        b = view.price.bar
        in_base = f" = {view.price.close_base:.2f} {base}" if view.price.close_base is not None else ""
        day = f", {view.day_change * 100:+.2f}% on the day" if view.day_change is not None else ""
        print(f"  last close {b.close:.2f} {b.currency}{in_base} ({b.date.isoformat()}{day})")

    print()
    f = view.fundamentals
    if view.fundamentals_note:
        print(f"  {view.fundamentals_note}")
    if f is not None:
        print(f"  fundamentals from {f.source}, {f.as_of.isoformat()}")
        for fig in FIGURES:
            value = _money(view.market_cap_base, base) if fig.name == "market_cap" else _format(getattr(f, fig.name), fig.kind)
            print(f"  {fig.label:<18}{value:>12}  {textwrap.shorten(fig.meaning, 200)}")

    print()
    if not view.signals:
        print(f"  no analyst signals yet: run `oracle analyse {p.symbol}`")
    else:
        print(f"  Analysts (refresh with `oracle analyse {p.symbol}`)")
        for stored in view.signals:
            print_signal(stored.signal, 4, "  OUT OF DATE" if stored.stale else "")

    for credit in credits([view.market_cap_rate_source, view.price.rate_source if view.price else None]):
        print(credit)
    if p.summary:
        print()
        print(textwrap.indent(textwrap.fill(p.summary, 88), "  "))
    return 0


def _format(value: float | None, kind: str) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:.2f}%" if kind == "pct" else f"{value:.2f}"


def _money(value: float | None, currency: str) -> str:
    if value is None:
        return "n/a"
    for size, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if abs(value) >= size:
            return f"{currency} {value / size:.2f}{suffix}"
    return f"{currency} {value:,.0f}"

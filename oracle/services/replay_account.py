"""What the replay study's verdicts would have earned as an account: the backtest engine, in
memory, trading each study date's verdicts under the AI account's rules, risk sizes and costs,
drawn against the same verdicts all taken as buys (what the rules alone make of the listings)
and the same listings bought in equal amounts and held. Nothing is written to the ledgers."""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import date

from oracle.broker.backtest import LeanSource, Replay, Trade, Venue, fill_costs, replay
from oracle.broker.compare import max_fall
from oracle.broker.fills import COSTS, buy_fill
from oracle.broker.rules import Lean
from oracle.market.bars import Bar, major_unit
from oracle.services.context import AppContext
from oracle.services.paper_service import Line, strategy_rules
from oracle.services.prices_service import ConvertedBar
from oracle.store.fx import FxRepo
from oracle.store.universe import UniverseRepo
from oracle.universe.exchanges import exchange_country

__all__ = ["BASELINES", "Account", "simulate"]

SOURCE = "ai"
BASELINES: dict[str, tuple[str, LeanSource]] = {}
"""Rule accounts drawn beside the verdicts' over the same listings, days and costs, by line
name: a caption and the leans they trade. quant-basics adds its monthly momentum baseline here."""

Call = tuple[date, str, float]
"""(as-of day, verdict, confidence)."""


@dataclass(frozen=True)
class Account:
    start: date
    """The study's first as-of day."""
    end: date
    """The last stored close."""
    capital: float
    """The AI account's size, which each line starts from."""
    currency: str
    """The base currency every amount is in."""
    listings: tuple[str, ...]
    """The study's listings the stored bars can price."""
    lines: tuple[Line, ...]
    """The verdicts' account (`ai`), every verdict a buy (`all_buy`), buy and hold, then any BASELINES."""
    trades: tuple[Trade, ...]
    """The verdicts' account's."""
    left_out: tuple[str, ...]
    """`SYMBOL: why`, for listings the stored bars cannot price."""
    notes: tuple[str, ...]


def simulate(ctx: AppContext, history: Mapping[str, Sequence[Bar]], first: Mapping[str, date],
             calls: Mapping[str, Sequence[Call]], start: date, end: date, left_out: Sequence[str],
             lagging: Mapping[str, date]) -> Account:
    """`history` is each priced listing's bars, from a year before `start` for the risk sizes;
    `first` its first as-of day; `calls` its verdicts, oldest first; `lagging` the last close
    of each listing whose bars stop well before `end` (held flat after it)."""
    base = ctx.config.base_currency
    rates, unrated = _rates(ctx, history, start)
    rows = {s: _priced(bars, rates[s]) for s, bars in history.items()}
    venues = {s: _venue(ctx, s) for s in history}
    rules, max_age, capital = strategy_rules(ctx), ctx.config.ai_account.max_verdict_age_days, ctx.config.risk.account

    def run(lean: LeanSource) -> Replay:
        # A verdict reads its day's news to 24:00 UTC, after most closes: it fills at the next close.
        return replay(rows, start, end, rules=rules, risk=asdict(ctx.config.risk), slippage=0.0, commission=0.0,
                      lean=lean, fill_at="close", venues=venues)

    def line(name: str, caption: str, r: Replay, label: str = "") -> Line:
        return Line(name, caption, _returns(r.equity, capital), max_fall(r.equity), len(r.trades),
                    sum(t.costs for t in r.trades), label)

    ai = run(_verdicts(calls, max_age))
    held, held_costs = _buy_and_hold(rows, first, venues, start, capital)
    n, unjudged = len(rows), [s for s in rows if not calls.get(s)]
    lines = (
        line("ai", "The replay verdicts, traded under the AI account's rules, risk sizes and costs.", ai),
        line("all_buy", "Every verdict a buy: the same replays and rules, each verdict taken as a buy at its "
                        "confidence, so AI against this line is what the calls themselves added.",
             run(_verdicts({s: [(d, "buy", c) for d, _, c in cs] for s, cs in calls.items()}, max_age)),
             "Every verdict a buy"),
        Line("buy_and_hold", f"Buy and hold: equal amounts of the {n} listing{'' if n == 1 else 's'}, bought at the "
                             "first close after the first as-of day, paying the same costs, and never sold.",
             _returns(held, capital), max_fall(held), sum(1 for s in rows if _entry(rows[s], first[s])), held_costs),
        *(line(name, caption, run(lean), name) for name, (caption, lean) in BASELINES.items()))
    notes = [
        f"The traded lines keep {ctx.config.risk.cash_floor:.0%} in cash and hold at most {rules.max_positions} "
        f"positions of at most {ctx.config.risk.max_position:.0%} each, sized by the risk analyst, selling at the "
        f"{rules.stop_loss:.0%} stop, and act on a verdict for {max_age} days; buy and hold is fully invested, so read "
        "the verdicts' effect as AI against every verdict a buy.",
        f"Fills pay the paper accounts' costs at the first close after the as-of day: {COSTS.slippage:.1%} slippage, "
        f"the broker's fee table for each listing's exchange, a {COSTS.fx_spread:.1%} FX spread each way for listings "
        f"not in {base} and {COSTS.stamp_duty:.1%} stamp duty on LSE buys.",
        f"Each listing's returns stay in its own currency, as the score's do: its {base} rate is held at the first "
        "day's, so exchange moves are left out.",
        "Listings are kept or left out, and priced on adjusted or plain closes, from their bars over the whole span, "
        "after the start too (the same for every line).",
        *([f"No stored exchange rate to {base} for {', '.join(unrated)}: priced as if a share cost 1 {base} on the "
           "first day, so their fees are approximate."] if unrated else []),
        *(f"{s}: prices only up to {d.isoformat()}, held flat after (unwatched listings are refetched about "
          "monthly)." for s, d in lagging.items()),
        *([f"No verdict for: {', '.join(unjudged)}."] if unjudged else []),
        *(f"Could not fill: {note}." for note in ai.rejected),
        *([f"Still waiting to fill on the last day: {', '.join(ai.unfilled)}."] if ai.unfilled else []),
    ]
    return Account(start, end, capital, base, tuple(rows), lines, ai.trades, tuple(left_out), tuple(notes))


def _returns(series: Sequence[tuple[date, float]], capital: float) -> tuple[tuple[date, float], ...]:
    return tuple((d, v / capital - 1) for d, v in series)


def _verdicts(calls: Mapping[str, Sequence[Call]], max_age: int) -> LeanSource:
    days = {s: [d for d, _, _ in rows] for s, rows in calls.items()}

    def lean(symbol: str, bars: Sequence[Bar]) -> Lean | None:
        today = bars[-1].date
        i = bisect_right(days.get(symbol, []), today) - 1
        if i < 0:
            return None
        as_of, label, confidence = calls[symbol][i]
        return Lean(symbol, as_of, label, confidence, SOURCE) if (today - as_of).days <= max_age else None

    return lean


def _venue(ctx: AppContext, symbol: str) -> Venue:
    profile = UniverseRepo(ctx.conn).profile(symbol)
    if profile is None:
        return Venue("", "")
    exchange = profile.exchange or ""
    return Venue(exchange_country(exchange) or profile.country or "", exchange)


def _rates(ctx: AppContext, history: Mapping[str, Sequence[Bar]], start: date) -> tuple[dict[str, float], list[str]]:
    """Each listing's major-currency units per base unit nearest `start`, held fixed; without a
    stored rate, its first close from `start`, so one share costs one base unit there."""
    base, fx = ctx.config.base_currency, FxRepo(ctx.conn, ctx.config.base_currency)
    out, unrated = {}, []
    for s, bars in history.items():
        currency, units = major_unit(bars[-1].currency)
        if currency == base:
            out[s] = 1.0
            continue
        # Only fees and whole shares read it, so the nearest stored day will do.
        picked = min(fx.history(currency), key=lambda r: abs(r.date - start), default=None)
        if picked is None:
            unrated.append(s)
            opening = next((b for b in bars if b.date >= start), bars[-1])
            out[s] = opening.close / units
        else:
            out[s] = picked.units_per_base
    return out, unrated


def _priced(bars: Sequence[Bar], rate: float) -> list[ConvertedBar]:
    """In the major currency unit, on adjusted closes when every bar has one."""
    adjusted = all(b.adj_close is not None and b.adj_close > 0 for b in bars)

    def scaled(b: Bar) -> Bar:
        currency, units = major_unit(b.currency)
        k = (b.adj_close / b.close if adjusted else 1.0) / units

        def by(v: float | None) -> float | None:
            return v * k if v is not None else None

        return replace(b, open=by(b.open), high=by(b.high), low=by(b.low), close=b.close * k, adj_close=b.close * k,
                       currency=currency)

    return [ConvertedBar(s, rate, s.close / rate) for s in map(scaled, bars)]


def _entry(rows: Sequence[ConvertedBar], first: date) -> ConvertedBar | None:
    return next((p for p in rows if p.bar.date > first), None)


def _buy_and_hold(rows: Mapping[str, Sequence[ConvertedBar]], first: Mapping[str, date],
                  venues: Mapping[str, Venue], start: date, capital: float) -> tuple[list[tuple[date, float]], float]:
    """Equal amounts, each bought in whole shares at its first close after its first as-of
    day with the paper costs, then held; cash until then, and its last close on days without one."""
    if not rows:
        return [], 0.0
    share, costs = capital / len(rows), 0.0
    shares: dict[str, tuple[date, int, float]] = {}
    for s, r in rows.items():
        p = _entry(r, first[s])
        if p is None:
            continue
        v = venues[s]
        fill = buy_fill(share, open_price=p.bar.close, rate=p.rate, country=v.country, exchange=v.exchange, costs=COSTS)
        shares[s] = (p.bar.date, fill.shares, share + fill.cash)
        costs += fill_costs(fill, p.rate)
    by_day: dict[date, dict[str, float]] = {}
    for s, r in rows.items():
        for p in r:
            if p.bar.date >= start:
                by_day.setdefault(p.bar.date, {})[s] = p.close_base
    last: dict[str, float] = {}
    out = []
    for d in sorted(by_day):
        last.update(by_day[d])
        value = 0.0
        for s in rows:
            if s in shares and d >= shares[s][0]:
                _, n, left = shares[s]
                value += n * last[s] + left
            else:
                value += share
        out.append((d, value))
    return out, costs

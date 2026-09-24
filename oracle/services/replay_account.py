"""What the replay study's verdicts would have earned as an account: the backtest engine, in
memory, trading each study date's verdicts under the AI account's rules and risk sizes, drawn
against the same listings bought in equal amounts and held. Nothing is written to the ledgers."""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import date

from oracle.broker.backtest import LeanSource, Replay, Trade, replay
from oracle.broker.compare import buy_and_hold, max_fall, returns_since
from oracle.broker.rules import Lean
from oracle.market.bars import Bar
from oracle.services.context import AppContext
from oracle.services.paper_service import Line, strategy_rules
from oracle.services.prices_service import ConvertedBar

__all__ = ["BASELINES", "Account", "simulate"]

BPS = 10_000
SLIPPAGE_BPS = 10.0
"""The backtest's default: the study's markets differ too much for one fee table."""
SOURCE = "ai"
BASELINES: dict[str, tuple[str, LeanSource]] = {}
"""Rule accounts drawn beside the verdicts' over the same listings and days, by line name: a
caption and the leans they trade. quant-basics adds its monthly momentum baseline here."""

Call = tuple[date, str, float]
"""(as-of day, verdict, confidence)."""


@dataclass(frozen=True)
class Account:
    start: date
    """The study's first as-of day."""
    end: date
    """The last stored close."""
    capital: float
    """The AI account's size, which each line starts from; amounts are in its units."""
    listings: tuple[str, ...]
    """The study's listings the stored bars price over the whole span."""
    lines: tuple[Line, ...]
    """The verdicts' account (`ai`), then buy and hold, then any BASELINES."""
    trades: tuple[Trade, ...]
    """The verdicts' account's."""
    left_out: tuple[str, ...]
    """`SYMBOL: why`, for listings the stored bars cannot price."""
    notes: tuple[str, ...]


def simulate(ctx: AppContext, history: Mapping[str, Sequence[Bar]], first: Mapping[str, date],
             calls: Mapping[str, Sequence[Call]], start: date, end: date, left_out: Sequence[str]) -> Account:
    """`history` is each priced listing's bars, from a year before `start` for the risk sizes;
    `first` its first as-of day; `calls` its verdicts, oldest first."""
    rows = {s: _indexed(bars) for s, bars in history.items()}
    rules, max_age = strategy_rules(ctx), ctx.config.ai_account.max_verdict_age_days
    capital = ctx.config.risk.account

    def run(lean: LeanSource) -> Replay:
        # A verdict reads its day's news to 24:00 UTC, after most closes: it fills at the next close.
        return replay(rows, start, end, rules=rules, risk=asdict(ctx.config.risk), slippage=SLIPPAGE_BPS / BPS,
                      commission=0.0, lean=lean, fill_at="close")

    def line(name: str, caption: str, r: Replay) -> Line:
        return Line(name, caption, _returns(r.equity, start), max_fall(r.equity), len(r.trades),
                    sum(t.costs for t in r.trades))

    ai = run(_verdicts(calls, max_age))
    held, _ = buy_and_hold({s: [(p.bar.date, p.bar.close) for p in r if p.bar.date > first[s]]
                            for s, r in rows.items()}, start, capital)
    n = len(rows)
    lines = (
        line("ai", "The replay verdicts, traded under the AI account's rules and risk sizes.", ai),
        Line("buy_and_hold", f"Buy and hold: equal amounts of the {n} listing{'' if n == 1 else 's'}, bought at the "
                             "first close after the first as-of day and never sold, with no costs.",
             _returns(held, start), max_fall(held), 0, 0.0),
        *(line(name, caption, run(lean)) for name, (caption, lean) in BASELINES.items()))
    notes = [
        "Each listing is indexed in its own currency, as the score's returns are: the study's currencies have no "
        "stored rates, so the account mixes them.",
        f"A verdict fills at the first close after its as-of day, {SLIPPAGE_BPS:g} basis points worse, with no "
        "commission, FX spread or stamp duty.",
        f"A verdict is acted on for {max_age} days after its as-of day, as the live AI account's are; a position is "
        f"held until a sell verdict or the {rules.stop_loss:.0%} stop.",
        *(f"Could not fill: {note}." for note in ai.rejected),
        *([f"Still waiting to fill on the last day: {', '.join(ai.unfilled)}."] if ai.unfilled else []),
    ]
    return Account(start, end, capital, tuple(rows), lines, ai.trades, tuple(left_out), tuple(notes))


def _returns(series: Sequence[tuple[date, float]], start: date) -> tuple[tuple[date, float], ...]:
    return tuple(returns_since(series, start)) if series else ()


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


def _indexed(bars: Sequence[Bar]) -> list[ConvertedBar]:
    """Priced from 1.0 at the first bar, so currencies and whole shares do not matter; on
    adjusted closes when every bar has one."""
    kept = [b for b in bars if b.close > 0]
    adjusted = all(b.adj_close is not None and b.adj_close > 0 for b in kept)
    base = (kept[0].adj_close if adjusted else kept[0].close) if kept else 1.0

    def scaled(b: Bar) -> Bar:
        k = (b.adj_close / b.close if adjusted else 1.0) / base

        def by(v: float | None) -> float | None:
            return v * k if v is not None else None

        return replace(b, open=by(b.open), high=by(b.high), low=by(b.low), close=b.close * k, adj_close=b.close * k)

    return [ConvertedBar(s, 1.0, s.close) for s in map(scaled, kept)]

"""Deterministic data-error checks (ai-track 03). Each reads plain rows and returns flags; nothing
here touches a database, and nothing anywhere edits market data because of a flag. Days are ISO
strings, as the bars table stores them. A bar is (day, open, high, low, close, adj_close, volume)."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from itertools import combinations

from oracle.store.testbed import Alias, YearTicker

__all__ = ["BAD_PRICE", "CHECKS", "DUPLICATE_CLASS", "JUNK_TICKER", "MEMBER_DAYS", "MISSING_DIVIDENDS", "RENAME_GAP",
           "SPLIT_JUMP", "STALE_BARS", "Alias", "Bar", "Flag", "dividend_steps", "duplicate_flags", "junk_flags",
           "member_day_flags", "rename_gap_flags", "series_flags", "stale_flags", "unadjusted_payer"]

SPLIT_JUMP, STALE_BARS, BAD_PRICE, JUNK_TICKER = "split_jump", "stale_bars", "bad_price", "junk_ticker"
MISSING_DIVIDENDS, RENAME_GAP, DUPLICATE_CLASS, MEMBER_DAYS = (
    "missing_dividends", "rename_gap", "duplicate_class", "member_days")
CHECKS = {
    SPLIT_JUMP: "Split-sized jump with no recorded split",
    STALE_BARS: "Stale or repeated bars",
    BAD_PRICE: "Zero or negative price",
    JUNK_TICKER: "Junk or fund ticker in a stock set",
    MISSING_DIVIDENDS: "Missing dividends",
    RENAME_GAP: "Rename gap",
    DUPLICATE_CLASS: "Duplicate share class",
    MEMBER_DAYS: "Testbed member-days missing by year",
}

Bar = tuple

SPLIT_RATIOS = (2, 3, 4, 5, 8, 10, 20, 50, 100)
SPLIT_TOLERANCE = 0.03
"""A day-to-day close ratio this close to a split ratio (or its inverse) is split-sized. No 3:2:
on the live copy every 1.5x or 0.67x day was real news, so an unrecorded 3:2 split goes unseen."""
SPLIT_NEAR_DAYS = 5
"""A recorded split this many calendar days from the jump explains it (sources date them a day apart)."""
SPLIT_GAP_DAYS = 10
"""Bars further apart than this are not compared: a jump across a gap in the data is no split."""
REPEAT_RUN = 5
"""Bars in a row with the same open, high, low and close that read as stale, not a quiet market."""
STALE_DAYS = 7
"""A series whose last bar is this many calendar days behind the newest is stale."""
STEP = 1e-5
"""A change in adj_close / close bigger than this marks a dividend (closes are split-adjusted
already). Small: NVDA's $0.01 a quarter is under 1e-4; Yahoo's float noise is about 1e-7."""
PAYER_STEPS = 4
"""Adjustments a series needs before it counts as a dividend payer."""
DIVIDEND_GAP_DAYS = 400
"""A payer's stretch with no adjustment this long (over a year, past one skipped quarter) is flagged."""
UNADJUSTED_MIN_BARS = 250
RENAME_WINDOW = timedelta(days=30)
WORST_SHOWN = 5

_FUND_NAME = re.compile(r"\b(ETF|ETN|UCITS)\b|\b(fund|ishares|spdr|index|acquisition corp\w*)\b", re.IGNORECASE)
# Warrants, units, rights and preferreds (Yahoo spelling), before any exchange suffix; index and FX symbols.
_JUNK_TICKER = re.compile(r"-(?:WT?|WS|U|UN|RT?|P[A-Z]?)(?:\.[A-Z]{1,3})?$|\.(?:WT|WS|U|UN|RT)(?:\.[A-Z]{1,3})?$"
                          r"|[\^=\s]|[a-z]")


@dataclass(frozen=True)
class Flag:
    check: str
    db: str
    """live or research."""
    symbol: str
    """"" when the flag is about no one listing (a year of the testbed)."""
    day: str | None
    detail: str
    explanation: str | None = None


def _d(day: str) -> date:
    return date.fromisoformat(day[:10])


def series_flags(db: str, symbol: str, bars: Sequence[Bar], splits: Mapping[str, Sequence[tuple[str, float]]]) -> list[Flag]:
    """Split-sized jumps, repeated bars, zero or negative prices and a payer's dividend gaps, in one
    pass over a symbol's bars (oldest first). `splits` is each symbol's recorded (day, ratio) list."""
    return [*_split_jumps(db, symbol, bars, splits.get(symbol, ())), *_repeats(db, symbol, bars),
            *_bad_prices(db, symbol, bars), *_dividend_gaps(db, symbol, bars)]


def _split_size(ratio: float) -> float | None:
    for n in SPLIT_RATIOS:
        for r in (n, 1 / n):
            if abs(ratio / r - 1) <= SPLIT_TOLERANCE:
                return r
    return None


def _explained(day: str, ratio: float, recorded: Iterable[tuple[str, float]]) -> bool:
    for when, r in recorded:
        if abs((_d(when) - _d(day)).days) <= SPLIT_NEAR_DAYS and r > 0 and any(
                abs(ratio * k - 1) <= 0.1 for k in (r, 1 / r)):
            return True
    return False


def _split_jumps(db: str, symbol: str, bars: Sequence[Bar], recorded) -> Iterable[Flag]:
    for prev, cur in zip(bars, bars[1:]):
        if not (prev[4] and cur[4]) or prev[4] <= 0 or cur[4] <= 0 or (_d(cur[0]) - _d(prev[0])).days > SPLIT_GAP_DAYS:
            continue
        ratio = cur[4] / prev[4]
        size = _split_size(ratio)
        if size is None or _explained(cur[0], ratio, recorded):
            continue
        said = f"1:{round(1 / size)}" if size < 1 else f"{size:g}:1"
        yield Flag(SPLIT_JUMP, db, symbol, cur[0], f"close {prev[4]:.4g} on {prev[0]} to {cur[4]:.4g} (x{ratio:.3f}, "
                                                  f"a {said} split's size) and no split recorded near it")


def _repeats(db: str, symbol: str, bars: Sequence[Bar]) -> Iterable[Flag]:
    start = 0
    for i in range(1, len(bars) + 1):
        if i < len(bars) and bars[i][1:5] == bars[start][1:5]:
            continue
        if (n := i - start) >= REPEAT_RUN:
            first, last = bars[start], bars[i - 1]
            quiet = " and no volume" if all(not b[6] for b in bars[start:i]) else ""
            yield Flag(STALE_BARS, db, symbol, first[0], f"{n} bars in a row with the same open, high, low and close "
                                                         f"({first[4]:.4g}){quiet}, {first[0]} to {last[0]}")
        start = i


def _bad_prices(db: str, symbol: str, bars: Sequence[Bar]) -> Iterable[Flag]:
    names = ("open", "high", "low", "close", "adj_close")
    for b in bars:
        bad = [f"{name} {v:g}" for name, v in zip(names, b[1:6]) if v is not None and v <= 0]
        if bad:
            yield Flag(BAD_PRICE, db, symbol, b[0], ", ".join(bad))


def dividend_steps(bars: Sequence[Bar]) -> list[str]:
    """Days where adj_close / close moved: an ex-dividend day (Yahoo's closes are split-adjusted)."""
    out, prev = [], None
    for b in bars:
        if not (b[4] and b[5]) or b[4] <= 0:
            continue
        f = b[5] / b[4]
        if prev is not None and abs(f / prev - 1) > STEP:
            out.append(b[0])
        prev = f
    return out


def _dividend_gaps(db: str, symbol: str, bars: Sequence[Bar]) -> Iterable[Flag]:
    steps = dividend_steps(bars)
    if len(steps) < PAYER_STEPS:
        return
    for a, b in zip(steps, [*steps[1:], bars[-1][0]]):
        if (gap := (_d(b) - _d(a)).days) > DIVIDEND_GAP_DAYS:
            to = "the last bar" if b == bars[-1][0] else "the next"
            yield Flag(MISSING_DIVIDENDS, db, symbol, a, f"no dividend adjustment for {gap} days, {a} to {b} ({to}), "
                                                         f"though the series shows {len(steps)} elsewhere")


def unadjusted_payer(db: str, symbol: str, bars: Sequence[Bar], payer_steps: Sequence[str]) -> Flag | None:
    """A series with adj_close equal to close throughout, while another copy of the same listing
    (`payer_steps`, its adjustment days) shows dividends inside its span."""
    if len(bars) < UNADJUSTED_MIN_BARS or dividend_steps(bars):
        return None
    first, last = bars[0][0], bars[-1][0]
    inside = [d for d in payer_steps if first < d <= last]
    if len(inside) < 2:
        return None
    return Flag(MISSING_DIVIDENDS, db, symbol, first, f"adj_close equals close on all {len(bars)} bars, {first} to "
                                                      f"{last}, though the other copy shows {len(inside)} dividend "
                                                      "adjustments in that span")


def stale_flags(db: str, last_days: Mapping[str, str | None], newest: str) -> list[Flag]:
    out = []
    for symbol, last in sorted(last_days.items()):
        if last is None:
            out.append(Flag(STALE_BARS, db, symbol, None, "no bars stored"))
        elif (behind := (_d(newest) - _d(last)).days) > STALE_DAYS:
            out.append(Flag(STALE_BARS, db, symbol, last, f"last bar {last}, {behind} days behind the newest ({newest})"))
    return out


def junk_flags(db: str, rows: Iterable[tuple[str, str | None, str | None]], need_listing: bool) -> list[Flag]:
    """(symbol, name, type) of a stock set's members. `need_listing`: a member with no universe
    listing is itself suspect (a watchlist); testbed members are judged by ticker and name only."""
    out = []
    for symbol, name, kind in rows:
        why = []
        if kind is not None and kind != "equity":
            why.append(f"listed as {kind}")
        if need_listing and kind is None and name is None:
            why.append("not in the universe")
        if name and _FUND_NAME.search(name):
            why.append(f"name reads as a fund or shell ({name})")
        if _JUNK_TICKER.search(symbol):
            why.append("ticker reads as a warrant, unit, right, preferred or index")
        if why:
            out.append(Flag(JUNK_TICKER, db, symbol, None, "; ".join(why)))
    return out


def rename_gap_flags(aliases: Iterable[Alias], member_days: Mapping[str, Sequence[str]],
                     bar_days: Callable[[str, str, str], set[str]]) -> list[Flag]:
    """Accepted renames whose member-days near the change have no bar, the old ticker's or the
    new one's (up to `priced_to`). `bar_days(ticker, start, end)` reads stored bar days."""
    out = []
    for a in aliases:
        days = member_days.get(a.old, ())
        if a.effective_day is not None:
            lo, hi = (a.effective_day - RENAME_WINDOW).isoformat(), (a.effective_day + RENAME_WINDOW).isoformat()
            days = [d for d in days if lo <= d <= hi]
        if not days:
            continue
        start, end = days[0], days[-1]
        upto = min(end, a.priced_to.isoformat()) if a.priced_to else end
        priced = bar_days(a.old, start, end) | (bar_days(a.new, start, upto) if upto >= start else set())
        missing = [d for d in days if d not in priced]
        if missing:
            when = f" on {a.effective_day}" if a.effective_day else ""
            out.append(Flag(RENAME_GAP, "research", a.old, missing[0],
                            f"{len(missing)} member-days with no bar from {a.old} or {a.new} (renamed{when}), "
                            f"{missing[0]} to {missing[-1]}"))
    return out


def duplicate_flags(db: str, pairs: Iterable[tuple[tuple[str, ...], str]],
                    member_days: Mapping[str, set[str]] | None) -> list[Flag]:
    """Listings of one company (`pairs`: tickers and why) in one stock set. With `member_days`, only
    tickers that were members on the same days count: one company renamed is not a duplicate."""
    out, seen = [], set()
    for tickers, why in pairs:
        for a, b in combinations(tickers, 2):
            if frozenset((a, b)) in seen:
                continue
            seen.add(frozenset((a, b)))
            if member_days is None:
                out.append(Flag(DUPLICATE_CLASS, db, a, None, f"{a} and {b} are both held: {why}"))
                continue
            both = sorted(member_days.get(a, set()) & member_days.get(b, set()))
            if both:
                out.append(Flag(DUPLICATE_CLASS, db, a, both[0], f"{a} and {b} both members on {len(both)} days, "
                                                                 f"{both[0]} to {both[-1]}: {why}"))
    return out


def member_day_flags(cells: Iterable[YearTicker]) -> list[Flag]:
    """Each testbed year with member-days that have no bar (renames mapped), naming the worst members."""
    years: dict[int, list[YearTicker]] = {}
    for c in cells:
        years.setdefault(c.year, []).append(c)
    out = []
    for year, cs in sorted(years.items()):
        total = sum(c.member_days for c in cs)
        missing = sum(c.member_days - c.with_bars_mapped for c in cs)
        if not missing:
            continue
        worst = sorted((c for c in cs if c.member_days > c.with_bars_mapped),
                       key=lambda c: (c.with_bars_mapped - c.member_days, c.ticker))
        named = ", ".join(f"{c.ticker} {c.member_days - c.with_bars_mapped}" for c in worst[:WORST_SHOWN])
        out.append(Flag(MEMBER_DAYS, "research", "", str(year),
                        f"{missing} of {total} member-days have no bar ({missing / total:.2%}), {len(worst)} members "
                        f"short; most missing: {named}"))
    return out

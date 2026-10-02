"""Fund comparison (boring-plan spec 2): all-world, developed world, S&P 500 and a UK home-bias mix
as sterling accumulating funds after their TER, from long-history proxies (US-listed ETFs with
their dividends reinvested, or the S&P 500 total-return index) converted at each day's GBPUSD. The
real UCITS fund is set beside its proxy where it exists, as a tracking check. Everything lives in
the research database, never the live one; each result is kept there beside the other tests."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import date, timedelta

from oracle.broker.fund_compare import Tracking, after_fees, blend, month_ends, tracking
from oracle.broker.trend_overlay import figures
from oracle.market import registry
from oracle.market.sources import (REFETCH_OVERLAP, AllFailed, Instrument, NoData, SourceFailed, first_success)
from oracle.market.yahoo import YahooPrices
from oracle.net.errors import CircuitOpen, RequestFailed
from oracle.services.context import AppContext
from oracle.services.dividends_service import PRICE_ONLY, TOTAL_RETURN, reinvested
from oracle.services.errors import InvalidQuery, NoPrices
from oracle.services.http import open_client
from oracle.services.prices_service import benchmark_history_in_base
from oracle.services.testbed_service import research_context
from oracle.store.bars import BarRepo
from oracle.store.db import to_utc_iso
from oracle.store.dividends import DividendRepo
from oracle.store.fx import FxRepo
from oracle.store.signal_tests import SignalTestRepo

__all__ = ["CHECKED", "FUNDS", "KEY", "Fund", "FundComparison", "FundRow", "FundSeries", "Proxy", "TrackRow",
           "compare_funds", "fund_comparison", "fund_series", "keep_funds", "run_and_keep_funds", "update_fund_data"]

KEY = "fund_comparison"
FIRST_FETCH = date(1985, 1, 1)
FX_FROM = date(1999, 1, 1)
"""Asked from here; Yahoo's GBPUSD=X starts 2003-12, the ECB's rates 1999-01."""
SETTLE = timedelta(days=90)
"""A new fund line's first weeks are thin on Yahoo (launch placeholders, stale zero-volume
prints: VUAG.L opened at 50.0 and 50.3, then 39.8), so tracking starts this long after its first."""
CHECKED = "2026-10-03"
"""When the TERs, proxy fees and US shares below were read from the funds' factsheets."""


@dataclass(frozen=True)
class Proxy:
    symbol: str
    label: str
    fee: float
    """The proxy's own yearly charge, already inside its price (0 for an index)."""
    index: bool = False
    """A total-return index: dividends are already in it, none to reinvest."""


@dataclass(frozen=True)
class Fund:
    key: str
    label: str
    ter: float
    """The UCITS fund's yearly charge (OCF/TER), taken day by day from the proxy."""
    us_share: float
    """Roughly how much of the fund is US shares, from its factsheet."""
    proxy: Proxy | None = None
    real: str | None = None
    """The real accumulating UCITS fund's London line, for the tracking check."""
    real_label: str | None = None
    parts: tuple[tuple[str, float], ...] = ()
    """A mix of other funds by key and weight, rebalanced yearly."""
    compared: bool = True
    """False for a fund shown only as part of a mix."""


_FUNDS = (
    Fund("all_world", "All-world", 0.0019, 0.62, Proxy("ACWI", "iShares MSCI ACWI ETF (MSCI ACWI)", 0.0032),
         "VWRP.L", "Vanguard FTSE All-World UCITS ETF Acc (VWRP)"),
    Fund("developed", "Developed world", 0.0020, 0.72, Proxy("URTH", "iShares MSCI World ETF (MSCI World)", 0.0024),
         "SWDA.L", "iShares Core MSCI World UCITS ETF Acc (SWDA)"),
    Fund("sp500", "S&P 500", 0.0007, 1.0, Proxy("^SP500TR", "S&P 500 total return index", 0.0, index=True),
         "VUAG.L", "Vanguard S&P 500 UCITS ETF Acc (VUAG)"),
    Fund("uk", "UK shares (part of the mix)", 0.0009, 0.0,
         Proxy("EWU", "iShares MSCI United Kingdom ETF (MSCI UK, for the FTSE All-Share)", 0.0050),
         "VUKG.L", "Vanguard FTSE 100 UCITS ETF Acc (VUKG)", compared=False),
)
MIX = (("all_world", 0.75), ("uk", 0.25))
FUNDS: dict[str, Fund] = {f.key: f for f in (
    *_FUNDS,
    Fund("uk_mix", "UK home-bias mix (75% all-world, 25% UK)",
         sum(w * next(f.ter for f in _FUNDS if f.key == k) for k, w in MIX),
         sum(w * next(f.us_share for f in _FUNDS if f.key == k) for k, w in MIX), parts=MIX),
)}


@dataclass(frozen=True)
class FundSeries:
    key: str
    label: str
    source: str
    """Where the numbers come from: always a proxy, said so."""
    kind: str
    """Total return, or price only when a proxy's dividends were never fetched."""
    ter: float
    points: tuple[tuple[date, float], ...]
    """Value in the base currency after the TER, 1.0 on the first day; monthly or daily."""


def fund_series(ctx: AppContext, key: str, monthly: bool = False) -> FundSeries:
    """A fund's value in the base currency (GBP), accumulating, after its TER, from its proxy as
    stored in the research database (run the fund comparison to fetch it). Daily, or with
    `monthly` each month's last day plus the first and last. Raises NoPrices when not stored."""
    fund = _fund(key)
    with research_context(ctx) as r:
        net, _, kind = _modelled(r, fund, ctx.clock.utcnow().date())
    if not net:
        raise NoPrices(f"no stored history for the {fund.label} proxy: run the fund comparison first")
    points = [(d, v / net[0][1]) for d, v in net]
    return FundSeries(key, fund.label, _source(fund), kind, fund.ter,
                      tuple(month_ends(points) if monthly else points))


def _fund(key: str) -> Fund:
    if key not in FUNDS:
        raise InvalidQuery(f"unknown fund {key!r}; known: {', '.join(FUNDS)}")
    return FUNDS[key]


def _source(fund: Fund) -> str:
    if fund.proxy is None:
        return "proxy mix: " + ", ".join(f"{w:.0%} {FUNDS[k].proxy.symbol}" for k, w in fund.parts)
    return f"proxy: {fund.proxy.symbol} ({fund.proxy.label})"


def _proxy_in_base(r: AppContext, proxy: Proxy, today: date) -> tuple[list[tuple[date, float]], bool]:
    """The proxy's closes in the base currency with its dividends reinvested, before `today` (the
    run day's bar may be mid-session); False when its dividends were never fetched."""
    rows = [c for c in benchmark_history_in_base(r, proxy.symbol) if c.bar.date < today]
    if proxy.index:
        return [(c.bar.date, c.close_base) for c in rows if c.close_base is not None], True
    tr = reinvested(r, proxy.symbol, rows)
    return [(d, v) for d, v in tr.closes if v is not None], tr.covered


def _modelled(r: AppContext, fund: Fund, today: date
              ) -> tuple[list[tuple[date, float]], list[tuple[date, float]], str]:
    """(after the fund's TER, before any charge, kind): the proxy modelled as the fund."""
    if fund.parts:
        got = [(_modelled(r, FUNDS[k], today), w) for k, w in fund.parts]
        kind = TOTAL_RETURN if all(m[2] == TOTAL_RETURN for m, _ in got) else PRICE_ONLY
        return blend([(m[0], w) for m, w in got]), blend([(m[1], w) for m, w in got]), kind
    closes, covered = _proxy_in_base(r, fund.proxy, today)
    return (after_fees(closes, fund.ter, fund.proxy.fee), after_fees(closes, 0.0, fund.proxy.fee),
            TOTAL_RETURN if covered else PRICE_ONLY)


def _real_in_base(r: AppContext, symbol: str, today: date) -> list[tuple[date, float]]:
    rows = [c for c in benchmark_history_in_base(r, symbol) if c.bar.date < today]
    return [(d, v) for d, v in reinvested(r, symbol, rows).closes if v is not None]


@dataclass(frozen=True)
class FundRow:
    key: str
    label: str
    source: str
    kind: str
    ret: float
    cagr: float
    max_fall: float
    ter: float
    ter_drag: float
    """Return lost to the TER over the window, as a ratio (0.01 is one point)."""
    us_share: float
    compared: bool
    own_start: date
    """The first day of the fund's own longest proxy history (in the base currency)."""
    own_ret: float
    own_cagr: float
    own_fall: float
    points: tuple[tuple[date, float], ...]
    """Return since the window's first day, monthly."""


@dataclass(frozen=True)
class TrackRow:
    key: str
    real: str
    real_label: str
    proxy: str
    start: date | None
    end: date | None
    real_return: float | None
    model_return: float | None
    difference: float | None
    yearly_difference: float | None
    """Real minus modelled; None when the real fund has no stored days inside the proxy's."""


@dataclass(frozen=True)
class FxCheck:
    day: date
    symbol: str
    close: float
    currency: str
    rate: float
    """Units of `currency` per unit of the base currency on `day`."""
    close_base: float


@dataclass(frozen=True)
class FundComparison:
    base: str
    start: date
    end: date
    """The longest history every compared fund's proxy shares."""
    rows: tuple[FundRow, ...]
    tracking: tuple[TrackRow, ...]
    fx: FxCheck | None
    checked: str
    run_at: str
    notes: tuple[str, ...]
    id: int | None = None


def compare_funds(ctx: AppContext) -> FundComparison:
    today = ctx.clock.utcnow().date()
    with research_context(ctx) as r:
        modelled = {k: _modelled(r, f, today) for k, f in FUNDS.items()}
        reals = {k: _real_in_base(r, f.real, today) for k, f in FUNDS.items() if f.real}
        missing = [FUNDS[k].proxy.symbol for k, f in FUNDS.items() if f.proxy and not modelled[k][0]]
        if missing:
            raise NoPrices(f"no stored history for {', '.join(missing)}: run the fund comparison to fetch it")
        start = max(m[0][0][0] for m in modelled.values())
        end = min(m[0][-1][0] for m in modelled.values())
        fx = _fx_check(r, FUNDS["all_world"].proxy.symbol, start)
    if start >= end:
        raise NoPrices(f"the proxies share no history ({start} to {end})")
    rows = tuple(_row(FUNDS[k], m, start, end) for k, m in modelled.items())
    tracks = tuple(_track(f, reals[k], modelled[k][0]) for k, f in FUNDS.items() if f.real)
    return FundComparison(ctx.config.base_currency, start, end, rows, tracks, fx, CHECKED,
                          to_utc_iso(ctx.clock.utcnow()), _notes(rows))


def _between(s, start: date, end: date) -> list[tuple[date, float]]:
    return [(d, v) for d, v in s if start <= d <= end]


def _row(fund: Fund, m, start: date, end: date) -> FundRow:
    net, gross, kind = m
    window, plain = _between(net, start, end), _between(gross, start, end)
    f, g, own = figures(window), figures(plain), figures(net)
    points = tuple((d, v / window[0][1] - 1) for d, v in month_ends(window))
    return FundRow(fund.key, fund.label, _source(fund), kind, f.ret, f.cagr, f.max_fall, fund.ter, g.ret - f.ret,
                   fund.us_share, fund.compared, net[0][0], own.ret, own.cagr, own.max_fall, points)


def _track(fund: Fund, real: list[tuple[date, float]], model: list[tuple[date, float]]) -> TrackRow:
    settled = [p for p in real if p[0] >= real[0][0] + SETTLE] if real else []
    t: Tracking | None = tracking(settled, model) if settled else None
    head = (fund.key, fund.real, fund.real_label, fund.proxy.symbol)
    if t is None:
        return TrackRow(*head, None, None, None, None, None, None)
    return TrackRow(*head, t.start, t.end, t.real_return, t.model_return, t.difference, t.yearly_difference)


def _fx_check(r: AppContext, symbol: str, day: date) -> FxCheck | None:
    c = next((c for c in benchmark_history_in_base(r, symbol) if c.bar.date >= day), None)
    if c is None or c.rate is None:
        return None
    return FxCheck(c.bar.date, symbol, c.bar.close, c.bar.currency, c.rate, c.close_base)


def _notes(rows: tuple[FundRow, ...]) -> tuple[str, ...]:
    out = [
        "Every line is a proxy modelled as the fund: the proxy's closes in sterling at each day's stored GBPUSD "
        "rate, dividends reinvested at the ex-date close, the proxy's own fee added back and the UCITS fund's TER "
        "taken day by day. The tracking table shows how far each real fund strayed from its model.",
        "The proxies track MSCI indexes (ACWI, World, UK) or the S&P 500; VWRP tracks FTSE All-World and VUKG the "
        "FTSE 100, so some tracking difference is the index, not the fund.",
        "Withholding tax: ^SP500TR reinvests dividends gross, while an Irish UCITS S&P 500 fund loses 15% of its US "
        "dividends (about 0.2 points a year). The US-listed proxies already lose foreign withholding in their price.",
        "Proxy closes are US closes and the real funds London closes, so day-to-day moves differ; returns over "
        "months and years compare fairly.",
        "The mix is rebalanced to its weights on each year's first shared day, without cost.",
        f"TERs, proxy fees and US shares from the funds' factsheets on {CHECKED}; a proxy's fee was higher in its "
        "early years, so adding back today's fee understates the model slightly there.",
        "No platform, dealing or FX fees here: those are the user's own (fees calculator).",
    ]
    short = [r.label for r in rows if r.kind != TOTAL_RETURN]
    if short:
        out.append(f"Price only (dividends not fetched): {', '.join(short)}.")
    return tuple(out)


def _symbols() -> list[str]:
    return [s for f in FUNDS.values() for s in ((f.proxy.symbol if f.proxy else None), f.real) if s]


async def update_fund_data(ctx: AppContext, say: Callable[[str], None] | None = None) -> list[str]:
    """Fetch every proxy and real fund from its last stored day on (that day again, as it may have
    been stored mid-session), their dividends, and the FX rates they need, into the research
    database. Returns what could not be fetched."""
    tell = say or (lambda _: None)
    failed: list[str] = []
    now = ctx.clock.utcnow()
    stamp = to_utc_iso(now)
    with research_context(ctx) as r:
        bars, divs = BarRepo(r.conn), DividendRepo(r.conn)
        async with open_client(r) as client:
            yahoo = YahooPrices(client)
            for symbol in _symbols():
                tell(f"fetching {symbol}")
                since = _resume(bars, divs, symbol)
                try:
                    chart = await yahoo.daily_chart(Instrument(symbol, None), since, now)
                except NoData:
                    continue
                except (SourceFailed, RequestFailed, CircuitOpen) as exc:
                    failed.append(f"{symbol}: {exc}")
                    continue
                bars.upsert(chart.bars, stamp)
                if chart.dividends is not None:
                    divs.replace(symbol, chart.dividends, since, now.date(), "yahoo", stamp)
                    divs.cover(symbol, since, now.date())
            base = ctx.config.base_currency
            fx = FxRepo(r.conn, base)
            chain = registry.fx_sources(ctx.config.fx.sources, client, ctx.config.keys)
            for currency in sorted(set(bars.currencies()) - {base}):
                tell(f"fetching {base}{currency} rates")
                last = fx.last_date(currency)
                since = FX_FROM if last is None else last - REFETCH_OVERLAP
                try:
                    rates, _ = await first_success(chain, lambda s: s.daily_rates(base, currency, since, now))
                except AllFailed as exc:
                    failed.append(f"{base}{currency} rates: {exc}")
                    continue
                fx.upsert(rates, stamp)
    return failed


def _resume(bars: BarRepo, divs: DividendRepo, symbol: str) -> date:
    """The last stored day, or the start when there is none or the stored bars were never asked
    for dividends from their first day (stored by another test, such as ^SP500TR by the trend one)."""
    last, span, stored = bars.last_date(symbol), divs.span(symbol), bars.span([symbol])
    if last is None or span is None or stored is None or span[0] > stored[0]:
        return FIRST_FETCH
    return last


def keep_funds(ctx: AppContext, c: FundComparison) -> FundComparison:
    raw = json.dumps(asdict(c), default=str)
    with research_context(ctx) as r:
        new_id = SignalTestRepo(r.conn).add(KEY, "SHOWN", raw, c.run_at)
    return _load(raw, new_id)


async def run_and_keep_funds(ctx: AppContext, say: Callable[[str], None] | None = None) -> FundComparison:
    """Update the stored series, compare and keep. A failed fetch is a note when days are stored."""
    failed = await update_fund_data(ctx, say)
    (say or (lambda _: None))("comparing the funds")
    c = compare_funds(ctx)
    if failed:
        c = replace(c, notes=(*c.notes, "Could not update " + "; ".join(failed) + "; the stored days were used."))
    return keep_funds(ctx, c)


def fund_comparison(ctx: AppContext) -> FundComparison | None:
    """The newest kept result."""
    with research_context(ctx) as r:
        row = next((x for x in SignalTestRepo(r.conn).latest() if x.signal == KEY), None)
    return None if row is None else _load(row.result, row.id)


def _load(raw: str, row_id: int) -> FundComparison:
    d = json.loads(raw)
    day = date.fromisoformat

    def opt(v):
        return None if v is None else day(v)

    rows = tuple(FundRow(**{**x, "own_start": day(x["own_start"]),
                            "points": tuple((day(a), v) for a, v in x["points"])}) for x in d["rows"])
    tracks = tuple(TrackRow(**{**x, "start": opt(x["start"]), "end": opt(x["end"])}) for x in d["tracking"])
    fx = None if d["fx"] is None else FxCheck(**{**d["fx"], "day": day(d["fx"]["day"])})
    return FundComparison(**{**d, "start": day(d["start"]), "end": day(d["end"]), "rows": rows, "tracking": tracks,
                             "fx": fx, "notes": tuple(d["notes"]), "id": row_id})

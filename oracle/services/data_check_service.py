"""Runs the data-error checks (`data_checks`) over the live and research databases, keeps the
newest run in the live database's meta table, and optionally asks a local Ollama model for a
one-paragraph explanation per flag. Market data is only read: a flag is never a fix."""

from __future__ import annotations

import json
import sqlite3
import time
from bisect import bisect_left
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, replace
from datetime import date
from itertools import groupby

from oracle.config import model_profiles
from oracle.research.llm import ChatModel
from oracle.services import data_checks as dc
from oracle.services.context import AppContext
from oracle.services.data_checks import CHECKS, Flag
from oracle.services.models_service import resolve_model, slot_profile
from oracle.services.testbed_service import BENCHMARKS, START, TAIL, Testbed, research_context
from oracle.store.db import to_utc_iso
from oracle.store.meta import MetaRepo
from oracle.store.testbed import Span, TestbedRepo

__all__ = ["EXPLAIN_MAX", "KEEP_ROWS", "LAST_RUN", "CheckResult", "DataCheckRun", "last_data_check", "run_data_checks"]

LAST_RUN = "data_checks_last_run"
KEEP_ROWS = 500
"""Flags kept per check for the drill-down; the count is always the full one."""
EXPLAIN_MAX = 25
"""Flags explained per run, taken a check at a time, so a local model never holds the job for long."""
OLLAMA = "ollama"
LIVE, RESEARCH = "live", "research"
_BARS = "SELECT symbol, date, open, high, low, close, adj_close, volume FROM bars ORDER BY symbol, date"

EXPLAIN_SYSTEM = (
    "You explain one data-quality flag in a stock price database to its owner. Write one plain-English paragraph "
    "of at most 80 words: what the flag most likely means, the most likely innocent cause, and what to check. "
    "Use only the facts given; do not guess prices, dates or events that are not in them.")
EXPLAIN_SCHEMA = {"type": "object", "properties": {"explanation": {"type": "string"}}, "required": ["explanation"]}


@dataclass(frozen=True)
class CheckResult:
    code: str
    label: str
    count: int
    rows: tuple[Flag, ...]
    """The first KEEP_ROWS flags."""


@dataclass(frozen=True)
class DataCheckRun:
    run_at: str
    seconds: float
    checks: tuple[CheckResult, ...]
    notes: tuple[str, ...] = ()
    explained: int = 0

    @property
    def total(self) -> int:
        return sum(c.count for c in self.checks)

    @property
    def summary(self) -> str:
        counts = ", ".join(f"{c.code} {c.count}" for c in self.checks)
        said = f"{self.total} flags ({counts})"
        return said + (f"; {self.explained} explained" if self.explained else "")

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @staticmethod
    def from_json(raw: str) -> DataCheckRun:
        d = json.loads(raw)
        checks = tuple(CheckResult(c["code"], c["label"], c["count"], tuple(Flag(**f) for f in c["rows"]))
                       for c in d["checks"])
        return DataCheckRun(d["run_at"], d["seconds"], checks, tuple(d.get("notes", ())), d.get("explained", 0))


def last_data_check(ctx: AppContext) -> DataCheckRun | None:
    raw = MetaRepo(ctx.conn).get(LAST_RUN)
    return DataCheckRun.from_json(raw) if raw else None


async def run_data_checks(ctx: AppContext, model: ChatModel | None = None,
                          say: Callable[[str], None] | None = None) -> DataCheckRun:
    """Every check over both databases, kept as the newest run. `model` stands in for the
    explaining profile's (tests); explanations run only with `[data_checks] explain` on."""
    say = say or (lambda _: None)
    started = time.monotonic()
    flags: dict[str, list[Flag]] = {code: [] for code in CHECKS}
    notes: list[str] = []
    research_steps: dict[str, list[str]] = {}
    splits: dict[str, list[tuple[str, float]]] = {}
    live_symbols = {r[0] for r in ctx.conn.execute("SELECT DISTINCT symbol FROM bars")}
    if ctx.config.research_db_path.is_file():
        say("research database")
        with research_context(ctx) as r:
            splits = _research(ctx, r, flags, live_symbols, research_steps)
    else:
        notes.append(f"no research database at {ctx.config.research_db_path}: its checks did not run")
    say("live database")
    _live(ctx, flags, research_steps, splits)
    explained = 0
    if ctx.config.data_checks.explain:
        say("explaining flags")
        explained = await _explain(ctx, flags, model, notes)
    results = tuple(CheckResult(code, CHECKS[code], len(fs), tuple(fs[:KEEP_ROWS])) for code, fs in flags.items())
    run = DataCheckRun(to_utc_iso(ctx.clock.utcnow()), round(time.monotonic() - started, 1), results, tuple(notes),
                       explained)
    MetaRepo(ctx.conn).set(LAST_RUN, run.to_json())
    return run


def _scan(conn: sqlite3.Connection, db: str, splits, flags: dict[str, list[Flag]],
          on_series: Callable[[str, list], None] | None = None,
          windows: dict[str, list[tuple[str, str]]] | None = None) -> None:
    """With `windows`, only bars inside a symbol's windows are checked, and symbols without any are skipped."""
    for symbol, rows in groupby(conn.execute(_BARS), key=lambda r: r[0]):
        bars = [tuple(r)[1:] for r in rows]
        if on_series:
            on_series(symbol, bars)
        if windows is not None:
            spans = windows.get(symbol, ())
            bars = [b for b in bars if any(lo <= b[0] <= hi for lo, hi in spans)]
        if bars:
            _add(flags, dc.series_flags(db, symbol, bars, splits))


def _research(ctx: AppContext, r: AppContext, flags: dict[str, list[Flag]], live_symbols: set[str],
              steps_out: dict[str, list[str]]) -> dict[str, list[tuple[str, float]]]:
    """The research checks; returns the recorded splits, which the live bars are checked against too.
    `steps_out` gets the dividend adjustment days of each symbol the live database also prices."""
    conn = r.conn
    splits: dict[str, list[tuple[str, float]]] = {}
    for ticker, day, ratio in conn.execute("SELECT ticker, day, ratio FROM tb_splits ORDER BY ticker, day"):
        splits.setdefault(ticker, []).append((day, ratio))

    def keep_steps(symbol: str, bars: list) -> None:
        if symbol in live_symbols:
            steps_out[symbol] = dc.dividend_steps(bars)

    tb, repo = Testbed(r), TestbedRepo(conn)
    calendar = [d.isoformat() for d in tb.calendar(START)]
    spans = [s for s in repo.spans() if s.end is None or s.end > START]
    aliases = tb.aliases()
    _scan(conn, RESEARCH, splits, flags, keep_steps, _windows(spans, aliases))
    members = sorted({s.ticker for s in spans} - set(BENCHMARKS))
    last = dict(conn.execute("SELECT symbol, MAX(date) FROM bars GROUP BY symbol").fetchall())
    current = sorted({s.ticker for s in spans if s.end is None} | set(BENCHMARKS))
    if calendar:
        _add(flags, dc.stale_flags(RESEARCH, {t: last.get(t) for t in current}, calendar[-1]))

    # Today's name and type of a former member's ticker are often a later holder's (FRX, a SPAC now):
    # only current members are judged by them, the rest by ticker alone.
    names = dict(conn.execute("SELECT ticker, name FROM xbrl_tickers").fetchall())
    listed = _listings(ctx.conn, current)
    rows = [(t, names.get(t) or listed.get(t, (None, None))[0], listed.get(t, (None, None))[1])
            if t in current else (t, None, None) for t in members]
    _add(flags, dc.junk_flags(RESEARCH, rows, need_listing=False))

    by_cik: dict[int, list[str]] = {}
    for m in repo.members():
        if m.cik is not None and m.ticker in members:
            by_cik.setdefault(m.cik, []).append(m.ticker)
    pairs = [*tb.duplicates(), *((tuple(sorted(ts)), f"one SEC filer, CIK {cik}")
                                 for cik, ts in sorted(by_cik.items()) if len(ts) > 1)]
    need = {t for p, _ in pairs for t in p} | {a.old for a in aliases}
    member_days = {t: _member_days(spans, t, calendar) for t in need}
    _add(flags, dc.duplicate_flags(RESEARCH, pairs, {t: set(d) for t, d in member_days.items()}))

    def bar_days(ticker: str, start: str, end: str) -> set[str]:
        return {d for (d,) in conn.execute("SELECT date FROM bars WHERE symbol = ? AND date BETWEEN ? AND ?",
                                           (ticker, start, end))}

    _add(flags, dc.rename_gap_flags(aliases, member_days, bar_days))
    cal_days = [date.fromisoformat(d) for d in calendar]
    _add(flags, dc.member_day_flags(repo.year_tickers(spans, cal_days)))
    return splits


def _live(ctx: AppContext, flags: dict[str, list[Flag]], research_steps: dict[str, list[str]], splits) -> None:
    def payer(symbol: str, bars: list) -> None:
        if (f := dc.unadjusted_payer(LIVE, symbol, bars, research_steps.get(symbol, ()))) is not None:
            flags[f.check].append(f)

    _scan(ctx.conn, LIVE, splits, flags, payer)
    conn = ctx.conn
    held = [(r[0], r[1], r[2], r[3]) for r in conn.execute(
        "SELECT DISTINCT i.symbol, l.name, l.type, l.company_key FROM watchlist_items i "
        "LEFT JOIN listings l ON l.symbol = i.symbol ORDER BY i.symbol")]
    _add(flags, dc.junk_flags(LIVE, [(s, n, t) for s, n, t, _ in held], need_listing=True))
    by_company: dict[str, list[str]] = {}
    for s, _, _, key in held:
        if key:
            by_company.setdefault(key, []).append(s)
    _add(flags, dc.duplicate_flags(LIVE, [(tuple(ts), f"one company in the universe ({key})")
                                          for key, ts in sorted(by_company.items()) if len(ts) > 1], None))
    newest = conn.execute("SELECT MAX(date) FROM bars").fetchone()[0]
    if newest:
        last = dict(conn.execute("SELECT symbol, MAX(date) FROM bars GROUP BY symbol").fetchall())
        watched = {s for s, *_ in held} | set(ctx.config.compare.benchmarks)
        _add(flags, dc.stale_flags(LIVE, {s: last.get(s) for s in watched}, newest))


def _windows(spans: list[Span], aliases) -> dict[str, list[tuple[str, str]]]:
    """The days the testbed reads each ticker's bars: its member spans plus the forward-return tail,
    a rename's new ticker up to `priced_to`, and the benchmarks whole. Bars outside are often a
    reused ticker's or an illiquid swap candidate's, and no test reads them."""
    out: dict[str, list[tuple[str, str]]] = {b: [("0000", "9999")] for b in BENCHMARKS}
    for s in spans:
        end = (s.end + TAIL).isoformat() if s.end else "9999"
        out.setdefault(s.ticker, []).append((s.start.isoformat(), end))
    for a in aliases:
        firsts = [lo for lo, _ in out.get(a.old, ())]
        if firsts:
            out.setdefault(a.new, []).append((min(firsts), a.priced_to.isoformat() if a.priced_to else "9999"))
    return out


def _listings(conn: sqlite3.Connection, symbols: list[str]) -> dict[str, tuple[str, str]]:
    out = {}
    for i in range(0, len(symbols), 500):
        chunk = symbols[i:i + 500]
        out.update({r[0]: (r[1], r[2]) for r in conn.execute(
            f"SELECT symbol, name, type FROM listings WHERE symbol IN ({','.join('?' * len(chunk))})", chunk)})
    return out


def _member_days(spans: list[Span], ticker: str, calendar: list[str]) -> list[str]:
    out: set[str] = set()
    for s in spans:
        if s.ticker == ticker:
            end = s.end.isoformat() if s.end else "9999-12-31"
            out.update(calendar[bisect_left(calendar, s.start.isoformat()):bisect_left(calendar, end)])
    return sorted(out)


def _add(flags: dict[str, list[Flag]], found: list[Flag]) -> None:
    for f in found:
        flags[f.check].append(f)


async def _explain(ctx: AppContext, flags: dict[str, list[Flag]], model: ChatModel | None, notes: list[str]) -> int:
    """Explanations for the first EXPLAIN_MAX flags, a check at a time. Only an Ollama profile is
    asked: the track pays for no API (ai-track decision 11)."""
    profile = ctx.config.data_checks.explain_model or slot_profile(ctx.config, "research")
    found = model_profiles(ctx.config).get(profile)
    if found is None or found.provider != OLLAMA:
        notes.append(f"no explanations: profile {profile} is "
                     f"{'unknown' if found is None else found.provider}, and only an Ollama profile is used")
        return 0
    try:
        chat = resolve_model(ctx, profile, model).model
    except Exception as exc:
        notes.append(f"no explanations: {exc}")
        return 0
    done = failed = 0
    for code, i in _round_robin(flags):
        f = flags[code][i]
        try:
            reply = await chat.reply(_explain_messages(f), EXPLAIN_SCHEMA)
            text = str(reply.get("explanation", "")).strip() if isinstance(reply, dict) else ""
        except Exception:
            text = ""
        if text:
            flags[code][i] = replace(f, explanation=text)
            done += 1
        else:
            failed += 1
    if failed:
        notes.append(f"{failed} explanations failed (no usable answer from {profile})")
    return done


def _round_robin(flags: dict[str, list[Flag]]) -> Iterator[tuple[str, int]]:
    n, depth = 0, 0
    while n < EXPLAIN_MAX:
        took = False
        for code, fs in flags.items():
            if depth < len(fs) and n < EXPLAIN_MAX:
                yield code, depth
                n, took = n + 1, True
        if not took:
            return
        depth += 1


def _explain_messages(f: Flag) -> list[dict]:
    facts = (f"Check: {CHECKS[f.check]}\nDatabase: {f.db}\nSymbol: {f.symbol or '(none)'}\nDay: {f.day or '(none)'}\n"
             f"Finding: {f.detail}")
    return [{"role": "system", "content": EXPLAIN_SYSTEM}, {"role": "user", "content": facts}]

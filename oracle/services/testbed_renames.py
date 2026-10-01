"""Renamed members and reused tickers, checked against SEC EDGAR and Yahoo.

A rename (FB -> META) is accepted only when two independent sources agree: SEC says the new
ticker's filer (its CIK) reported under the old ticker before the change and under the new one
after (each report's cover dei:TradingSymbol, else its document's name), and Yahoo's series under
the new ticker covers the old one's member-days and runs across the change. Chains resolve to the
last live ticker (SYMC -> NLOK -> GEN). Pairs failing either check are kept for review, never applied.

Candidates: the membership's same-day swaps (a member leaves the day another joins), and for a
former member Yahoo never priced, the company Yahoo's search names under its old ticker (GPS ->
"The Gap, Inc." -> SEC's GAP), renamed after it left.

Identity: whether today's holder of a former member's ticker is that member. SEC decides when it
can (the filer first filed after the member joined, or its cover named another ticker then: another
company; its report then named the ticker: the same); otherwise the build's first-trade rule stands."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from bisect import bisect_left
from dataclasses import dataclass, field
from datetime import date, timedelta

import httpx

from oracle.market.bars import Bar
from oracle.research import edgar
from oracle.store.bars import BarRepo
from oracle.store.testbed import Alias, Span, TestbedRepo

SAME, OTHER = "same", "other"
NOT_ON_YAHOO = "not on Yahoo"
REUSED = "ticker reused by another instrument"
NAMED = 3
"""Companies Yahoo's search names under an old ticker that are checked, at most."""
NEED = 0.9
"""A former member priced on under this share of its member-days is looked at for a rename."""
YAHOO_COVER = 0.9
"""Share of the old ticker's member-days the new ticker's series must price."""
AROUND = 10
"""Trading days each side of the change the new series must run on (most of them)."""
REACH = timedelta(days=400)
"""How far from the change a report is looked for: a year of quarters and a late annual report."""
TRIES = 4
"""Reports read on each side before the side is said to name no ticker."""
JOINED_SLACK = timedelta(days=30)
REUSED_AFTER = timedelta(days=7)
"""Bars of the old ticker this long after the change mean Yahoo still lists something under it."""

_NAME_WORDS = re.compile(r"[^a-z0-9 ]+")
_SUFFIXES = {"inc", "incorporated", "corp", "corporation", "co", "company", "plc", "ltd", "limited", "nv", "sa", "ag",
             "lp", "llc", "the", "de", "group"}


@dataclass(frozen=True)
class Candidate:
    old: str
    new: str
    day: date | None
    """The index's swap day; None for a rename found by name after the old ticker left."""
    one_for_one: bool
    """The only ticker out and the only one in that day."""


@dataclass
class RenameRun:
    accepted: int = 0
    rejected: int = 0
    same: int = 0
    other: int = 0
    refetch: list[str] = field(default_factory=list)
    """Former members SEC says are today's holders of their ticker, noted reused by the first-trade rule."""
    dropped: list[str] = field(default_factory=list)
    """Former members whose stored bars SEC says are another company's."""
    same_company: list[tuple[str, str, str]] = field(default_factory=list)

    @property
    def summary(self) -> str:
        return (f"{self.accepted} renames accepted, {self.rejected} kept for review; SEC identity: {self.same} same "
                f"company, {self.other} another ({len(self.dropped)} bars dropped, {len(self.refetch)} refetched)")


def normal(ticker: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", ticker.upper())


def company_name(name: str) -> str:
    """A company's name for matching: lower case, no punctuation or legal suffixes."""
    words = _NAME_WORDS.sub(" ", name.lower().replace("&", " and ")).split()
    return " ".join(w for w in words if w not in _SUFFIXES)


def swap_candidates(spans: list[Span], need: set[str]) -> list[Candidate]:
    """Each ticker in `need` that left on a day paired with every ticker that joined that day."""
    joined: dict[date, list[str]] = {}
    left: dict[date, list[str]] = {}
    for s in spans:
        joined.setdefault(s.start, []).append(s.ticker)
        if s.end:
            left.setdefault(s.end, []).append(s.ticker)
    out = []
    for day, gone in sorted(left.items()):
        came = sorted(set(joined.get(day, [])) - set(gone))
        for old in sorted(t for t in gone if t in need):
            out += [Candidate(old, new, day, len(gone) == 1 and len(came) == 1) for new in came]
    return out


class Sec:
    """SEC EDGAR reads for one run, each asked once: the ticker map, filers' submissions and
    their reports' covers. `get` answers None for a 404."""

    def __init__(self, get: Callable[[str], Awaitable[httpx.Response | None]]) -> None:
        self._get = get
        self._map: dict[str, tuple[int, str]] | None = None
        self._bodies: dict[int, object] = {}
        self._pages: dict[str, list[edgar.Report]] = {}
        self._readings: dict[str, tuple[tuple[str, ...], str | None]] = {}

    async def ticker_map(self) -> dict[str, tuple[int, str]]:
        if self._map is None:
            resp = await self._get(edgar.TICKERS_URL)
            self._map = edgar.parse_tickers(resp.json()) if resp is not None else {}
        return self._map

    async def cik(self, ticker: str) -> int | None:
        found = (await self.ticker_map()).get(ticker.upper()) or (await self.ticker_map()).get(ticker.replace("-", "."))
        return found[0] if found else None

    async def tickers_of(self, cik: int) -> list[str]:
        return [t for t, (c, _) in (await self.ticker_map()).items() if c == cik]

    async def title(self, cik: int) -> str:
        return next((name for c, name in (await self.ticker_map()).values() if c == cik), f"CIK {cik}")

    async def body(self, cik: int) -> object:
        if cik not in self._bodies:
            resp = await self._get(edgar.submissions_url(cik))
            self._bodies[cik] = resp.json() if resp is not None else {}
        return self._bodies[cik]

    async def reports(self, cik: int, start: date, end: date) -> list[edgar.Report]:
        """Its periodic reports filed from `start` to `end`, oldest first."""
        body = await self.body(cik)
        found = edgar.parse_reports(edgar.recent(body))
        for url in edgar.pages_between(body, start, end):
            if url not in self._pages:
                resp = await self._get(url)
                self._pages[url] = edgar.parse_reports(resp.json()) if resp is not None else []
            found += self._pages[url]
        return sorted({r.accession: r for r in found if start <= r.filed <= end}.values(), key=lambda r: r.filed)

    async def reading(self, cik: int, report: edgar.Report) -> tuple[tuple[str, ...], str | None]:
        """The tickers a report names and how: its cover's trading symbols, else its document's name."""
        if report.accession not in self._readings:
            resp = await self._get(edgar.cover_url(cik, report.accession))
            symbols = edgar.trading_symbols(resp.text) if resp is not None else ()
            if symbols:
                self._readings[report.accession] = (symbols, "cover")
            elif report.document_ticker:
                self._readings[report.accession] = ((report.document_ticker,), "document name")
            else:
                self._readings[report.accession] = ((), None)
        return self._readings[report.accession]

    async def first_reading(self, cik: int, reports: list[edgar.Report]):
        """The first of `reports` (in the order given) naming a ticker: (report, tickers, how), or None."""
        for r in reports[:TRIES]:
            symbols, how = await self.reading(cik, r)
            if symbols:
                return r, symbols, how
        return None


def first_trade_note(t: str) -> str:
    """How the build's first-trade rule notes a former member's ticker as reused."""
    return f"{REUSED}: Yahoo's {t} first traded"


def said(reading) -> str:
    r, symbols, how = reading
    return f"{r.form} {r.filed} {how} {'/'.join(symbols)}"


class Renames:
    """The rename and identity check over a built testbed."""

    def __init__(self, repo: TestbedRepo, bars: BarRepo, calendar: list[date], sec: Sec,
                 chart: Callable[[str], Awaitable[list[Bar] | None]],
                 search: Callable[[str], Awaitable[list[str]]], at: str, tail: timedelta, start: date,
                 progress: Callable[[str], None] = lambda _: None) -> None:
        self.repo, self.bars, self.calendar, self.sec = repo, bars, calendar, sec
        self.chart, self.search, self.at, self.tail, self.start = chart, search, at, tail, start
        self.progress = progress
        self.spans: dict[str, list[Span]] = {}
        for s in repo.spans():
            self.spans.setdefault(s.ticker, []).append(s)
        self.members = {m.ticker: m for m in repo.members()}
        self.run = RenameRun()
        self._series: dict[str, set[date]] = {}
        self._cik_of: dict[str, int] = {}
        self._same_cik: dict[str, int] = {}

    def former(self, t: str) -> bool:
        return all(s.end is not None for s in self.spans.get(t, []))

    def member_days(self, t: str) -> list[date]:
        out: list[date] = []
        for s in self.spans.get(t, []):
            out += self.calendar[bisect_left(self.calendar, max(s.start, self.start)):
                                 bisect_left(self.calendar, s.end or date.max)]
        return out

    def last_day(self, t: str) -> date:
        return max(s.end for s in self.spans[t])

    async def check(self) -> RenameRun:
        # Identity first: a former member whose bars prove another company's (BBT, now Beacon) is then
        # a rename candidate like one never priced (BBT -> TFC).
        await self._identity()
        need = set(self.run.dropped)
        for t in self.spans:
            days = self.member_days(t)
            if days and self.former(t):
                own = {b.date for b in self.bars.since(t, days[0], days[-1])}
                if len(own.intersection(days)) < NEED * len(days):
                    need.add(t)
        aliases = await self._renames(need)
        self.repo.save_aliases(aliases)
        await self._same_company(aliases)
        return self.run

    async def _renames(self, need: set[str]) -> list[Alias]:
        found: dict[str, Alias] = {}
        """old -> accepted alias, chains resolved."""
        cik_of: dict[str, int] = {}
        kept: list[Alias] = []
        swaps = swap_candidates([s for ss in self.spans.values() for s in ss], need)
        # Latest first, so a chain's later link (NLOK -> GEN) is known when its earlier one (SYMC -> NLOK) is checked.
        for i, c in enumerate(sorted(swaps, key=lambda c: (c.day, c.old, c.new), reverse=True), 1):
            if c.old in found:
                continue
            self.progress(f"renames: {c.old} -> {c.new} ({i}/{len(swaps)})")
            alias, _ = await self._verify(c, found, cik_of)
            if alias.accepted:
                found[c.old] = alias
            if alias.accepted or c.one_for_one:
                kept.append(alias)
        for t in sorted(need - set(found)):
            if (self.members[t].bars_note or "").startswith((NOT_ON_YAHOO, REUSED)) or t in self.run.dropped:
                for new in await self._named(t):
                    self.progress(f"renames after leaving: {t} -> {new}")
                    alias, partly = await self._verify(Candidate(t, new, None, False), found, cik_of)
                    # A name match neither source backs is another company of a similar name: not worth review.
                    if partly:
                        kept.append(alias)
                    if alias.accepted:
                        found[t] = alias
                        break
        self.run.accepted = sum(a.accepted for a in kept)
        self.run.rejected = len(kept) - self.run.accepted
        self._cik_of = cik_of
        return kept

    async def _named(self, old: str) -> list[str]:
        """Today's SEC tickers of the companies Yahoo's search names under `old`."""
        names = {company_name(n) for n in await self.search(old)} - {""}
        out = []
        for t, (cik, title) in (await self.sec.ticker_map()).items():
            if company_name(title) in names and normal(t) != normal(old) and cik not in {c for c, _ in out}:
                out.append((cik, t))
        return [t for _, t in out][:NAMED]

    async def _verify(self, c: Candidate, found: dict[str, Alias], cik_of: dict[str, int]) -> tuple[Alias, bool]:
        """The pair checked, and whether either source backed it."""
        final, chain = c.new, [c.new]
        while final in found:
            final = found[final].new
            chain.append(final)
        cik = cik_of.get(c.new) or await self.sec.cik(c.new) or await self.sec.cik(final)
        effective = c.day
        sec_ok, sec_said = False, f"SEC: no filer has {' or '.join(dict.fromkeys((c.new, final)))} today"
        if cik:
            title = await self.sec.title(cik)
            pivot = c.day or self.last_day(c.old)
            before = await self.sec.first_reading(cik, list(reversed(await self.sec.reports(cik, pivot - REACH,
                                                                                          pivot - timedelta(1)))))
            if c.day:
                after = await self._after(cik, c.old, c.new, await self.sec.reports(cik, c.day, c.day + REACH))
            else:
                after = await self._first_naming(cik, c.new, pivot)
                effective = after[0].filed if after else None
            names_old = before is not None and normal(c.old) in map(normal, before[1])
            names_new = after is not None and normal(c.new) in map(normal, after[1])
            sec_ok = names_old and names_new
            parts = [said(before) if before else f"no report naming a ticker in the year before {pivot}",
                     said(after) if after else f"no report naming {c.new} after {pivot}"]
            sec_said = f"SEC CIK {cik} ({title}): {'; '.join(parts)}"
            if not names_old:
                sec_said += f" (not {c.old})"
            elif not names_new:
                sec_said += f" (not {c.new})"
        yahoo_ok, yahoo_said = await self._yahoo(c, final)
        accepted = sec_ok and yahoo_ok
        if accepted and cik:
            cik_of[c.old] = cik
        how = "swap" if c.day else "renamed after leaving (found by name)"
        evidence = (f"{'accepted' if accepted else 'rejected'} ({how}{', chain ' + ' -> '.join([c.old, *chain]) if len(chain) > 1 else ''}): "
                    f"{sec_said}. {yahoo_said}")
        return Alias(c.old, final, effective, accepted, evidence, self.at,
                     self.last_day(c.old) + self.tail if accepted else None), sec_ok or yahoo_ok

    async def _after(self, cik: int, old: str, new: str, reports: list[edgar.Report]):
        """The first report after the change naming a ticker other than `old`: a cover can still name the
        old ticker for a report filed days after the change (Ball's 10-Q of 2022-05-05 says BLL)."""
        for r in reports[:2 * TRIES]:
            symbols, how = await self.sec.reading(cik, r)
            if symbols and not (normal(old) in map(normal, symbols) and normal(new) not in map(normal, symbols)):
                return r, symbols, how
        return None

    async def _first_naming(self, cik: int, new: str, since: date):
        """The first report from `since` naming `new` (reports name the old ticker, then the new one)."""
        reports = await self.sec.reports(cik, since, date.max)
        lo, hi, hit = 0, len(reports), None
        while lo < hi:
            mid = (lo + hi) // 2
            symbols, how = await self.sec.reading(cik, reports[mid])
            if normal(new) in map(normal, symbols):
                hit, hi = (reports[mid], symbols, how), mid
            else:
                lo = mid + 1
        return hit

    async def _days_of(self, ticker: str) -> set[date]:
        if ticker not in self._series:
            days = {b.date for b in self.bars.since(ticker, self.start)}
            if not days and ticker not in self.members:
                days = {b.date for b in await self.chart(ticker) or []}
            self._series[ticker] = days
        return self._series[ticker]

    async def _yahoo(self, c: Candidate, final: str) -> tuple[bool, str]:
        days = self.member_days(c.old)
        series = await self._days_of(final)
        if not series:
            return False, f"Yahoo: no bars for {final}"
        covered = len(series.intersection(days))
        ok = covered >= YAHOO_COVER * len(days)
        said_ = f"Yahoo: {final} priced on {covered} of {c.old}'s {len(days)} member-days"
        if c.day:
            i = bisect_left(self.calendar, c.day)
            near = self.calendar[max(0, i - AROUND):i + AROUND]
            runs = sum(d in series for d in near) >= 0.8 * len(near)
            ok &= runs
            said_ += f", {'runs' if runs else 'does not run'} across {c.day}"
            # Bars SEC just proved another company's are dropped after this check, so they say nothing here.
            late = [] if c.old in self.run.dropped else self.bars.since(c.old, c.day + REUSED_AFTER)
            if late:
                ok = False
                said_ += f"; Yahoo still lists {c.old} on {late[0].date}"
        return ok, said_

    async def _identity(self) -> None:
        """Former members with bars, or noted reused by the first-trade rule: SEC's say where it has one."""
        for t, m in sorted(self.members.items()):
            if not self.spans.get(t) or not self.former(t):
                continue
            first_trade = (m.bars_note or "").startswith(first_trade_note(t))
            if not (first_trade or (m.bars_note is None and m.bars_tried_at)):
                continue
            self.progress(f"identity: {t}")
            verdict, note, cik = await self._judge(t)
            self.repo.set_identity(t, verdict, note, cik)
            if verdict == SAME:
                self._same_cik[t] = cik
                self.run.same += 1
                if first_trade:
                    self.run.refetch.append(t)
            elif verdict == OTHER:
                self.run.other += 1
                if m.bars_note is None:
                    self.run.dropped.append(t)

    async def _judge(self, t: str) -> tuple[str | None, str, int | None]:
        cik = await self.sec.cik(t)
        if not cik:
            return None, f"SEC has no filer with {t} today", None
        title = await self.sec.title(cik)
        joined = max(min(s.start for s in self.spans[t]), self.start)
        first = edgar.first_filed(await self.sec.body(cik))
        # A new holding company keeps its ticker and Yahoo series (Perrigo plc, 2013): a late filer is
        # another company only when Yahoo's own series under the ticker starts late too, or is not kept.
        bars_from = self.bars.first_between(t, self.start, date.max)
        if first and first > joined + JOINED_SLACK and (bars_from is None or bars_from > joined + REUSED_AFTER):
            return OTHER, (f"SEC's {t} is CIK {cik} ({title}), whose first filing ({first}) is after {t} joined the "
                           f"index ({joined})"), cik
        left = self.last_day(t)
        reading = await self.sec.first_reading(cik, list(reversed(await self.sec.reports(cik, joined, left))))
        if reading is None:
            return None, f"SEC's {t} is CIK {cik} ({title}), no report naming a ticker while {t} was a member", cik
        named = normal(t) in map(normal, reading[1])
        if named:
            return SAME, f"SEC's {t} is CIK {cik} ({title}); its {said(reading)} while a member", cik
        if reading[2] == "cover":
            return OTHER, f"SEC's {t} is CIK {cik} ({title}); its {said(reading)} while {t} was a member", cik
        return None, f"SEC's {t} is CIK {cik} ({title}); its {said(reading)}, a document name only", cik

    async def _same_company(self, aliases: list[Alias]) -> None:
        """Pairs of members on the same days that are one company: one SEC filer, or one series."""
        series = {a.old: a.new for a in aliases if a.accepted}
        listed = await self.sec.ticker_map()
        ciks: dict[str, int] = {}
        for t in self.members:
            if not self.spans.get(t):
                continue
            if t in self._cik_of:
                ciks[t] = self._cik_of[t]
            elif t in self._same_cik:
                ciks[t] = self._same_cik[t]
            elif not self.former(t) and (cik := await self.sec.cik(t)):
                ciks[t] = cik
        self.repo.set_ciks(ciks)
        by_cik: dict[int, list[str]] = {}
        for t, cik in ciks.items():
            by_cik.setdefault(cik, []).append(t)
        for cik, ts in sorted(by_cik.items()):
            for i, a in enumerate(sorted(ts)):
                for b in sorted(ts)[i + 1:]:
                    if a in listed and b in listed and series.get(a, a) != series.get(b, b):
                        continue  # two share classes listed today (GOOG, GOOGL): two series
                    if self._overlap(a, b):
                        self.run.same_company.append((a, b, f"one SEC filer, CIK {cik} ({await self.sec.title(cik)})"))

    def _overlap(self, a: str, b: str) -> bool:
        return any(x.start < (y.end or date.max) and y.start < (x.end or date.max)
                   for x in self.spans[a] for y in self.spans[b])


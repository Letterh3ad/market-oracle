"""JSON shapes more than one route returns, so every tab reads them the same way."""

from __future__ import annotations

from dataclasses import asdict
from urllib.parse import urlsplit

from oracle.services.activity_service import Job
from oracle.services.analysis_service import WatchedAnalysis
from oracle.services.backtest_service import KeptBacktest
from oracle.services.cutoff_service import CutoffProbe, ReplayCutoff
from oracle.services.data_check_service import DataCheckRun
from oracle.services.discovery_chat_service import AnswerView
from oracle.services.discovery_service import DiscoveryEvidence
from oracle.services.fx_service import FxUpdate, credits
from oracle.services.history_service import NOISY, THIN, Coverage
from oracle.services.laya_test_service import LayaTest
from oracle.services.paper_service import Line, Order
from oracle.services.regime_service import LineStep, off_note
from oracle.services.replay_service import StudyStatus
from oracle.services.research_service import UNVALIDATED, ResearchRun
from oracle.services.schedule_service import ScheduleView, Upcoming
from oracle.services.signal_test_service import SignalTest
from oracle.services.testbed_service import UNDER_PRICED, TestbedCoverage
from oracle.services.tick_service import KIND_LABELS, TickRun
from oracle.services.fund_compare_service import FundComparison
from oracle.services.trend_service import TrendLine, TrendTest, TrendWindow


def lean(lean, stale: bool) -> dict | None:
    return None if lean is None else {
        "label": lean.label, "probability": lean.probability, "source": lean.source,
        "as_of": lean.as_of, "stale": stale,
    }


def next_step(has_price: bool, lean, stale: bool) -> str | None:
    """What a listing needs before its view is complete, as the button that does it."""
    if not has_price:
        return "update prices"
    if lean is None or stale:
        return "analyse"
    return None


def tick(run: TickRun | None) -> dict | None:
    return None if run is None else {
        "kind": run.kind, "label": KIND_LABELS[run.kind], "trigger": run.trigger, "started_at": run.started_at, "finished_at": run.finished_at,
        "outcome": run.outcome, "first_error": run.first_error, "note": run.note,
        "stages": [asdict(s) for s in run.stages],
    }


def job(j: Job) -> dict:
    return {
        "id": j.id, "kind": j.kind, "label": j.label, "subject": j.subject, "link": j.link, "state": j.state,
        "automatic": j.automatic, "profile": j.profile,
        "progress": j.progress(), "error": j.error, "submitted_at": j.submitted_at, "started_at": j.started_at,
        "finished_at": j.finished_at, "result": _result(j) if j.state == "done" else None,
    }


def _prices(run) -> dict:
    return {"listings": [asdict(o) for o in run.update.listings], "fx": [asdict(f) for f in run.update.fx],
            "fills": [order_outcome(f.order, f.status, f.note) for f in run.fills]}


def _analysed(runs: list[WatchedAnalysis]) -> dict:
    return {"listings": [
        {"symbol": r.symbol, "error": r.error,
         "lean": None if r.analysis is None or r.analysis.lean is None
         else {"label": r.analysis.lean.label, "probability": r.analysis.lean.probability}}
        for r in runs
    ]}


def _fx(updates: list[FxUpdate]) -> dict:
    return {"updates": [asdict(u) for u in updates], "credits": credits(u.source for u in updates)}


def line(l: Line) -> dict:
    """A comparison line with its return, which `asdict` leaves out (a property)."""
    return {**asdict(l), "return": l.return_}


def backtest_summary(k: KeptBacktest) -> dict:
    """Enough to list and pick a kept run: its lines' figures without their points."""
    b = k.backtest
    return {"id": k.id, "ran_at": k.ran_at, "base_currency": k.base_currency, "watchlist": b.watchlist,
            "start": b.start, "end": b.end, "slippage_bps": b.slippage_bps, "commission": b.commission,
            "lines": [{"name": l.name, "return": l.return_, "max_fall": l.max_fall, "trades": l.trades,
                       "costs": l.costs} for l in b.lines]}


def backtest(k: KeptBacktest) -> dict:
    b = k.backtest
    return {**backtest_summary(k), "listings": list(b.listings), "lines": [line(l) for l in b.lines],
            "trades": [asdict(t) for t in b.trades], "notes": list(b.notes),
            "walk_forward": None if b.walk_forward is None else asdict(b.walk_forward)}


def answer(v: AnswerView) -> dict:
    """One answer to a question: its shortlist with evidence, what was left off and why, and
    what changed since the answer it is compared with."""
    a = v.answer
    return {
        "id": a.id, "question_id": a.question_id, "model": a.model, "started_at": a.started_at,
        "finished_at": a.finished_at, "status": a.status, "stage": a.stage, "error": a.error,
        "searches": list(a.searches), "notes": list(a.notes), "compared_to": v.compared_to,
        "shortlist": [{**asdict(line.item), "new": line.new, "watched": line.watched,
                       "discovered": line.discovered_as is not None, "discovered_as": line.discovered_as,
                       "evidence": [headline(e) for e in line.evidence]}
                      for line in v.shortlist],
        "skipped": [asdict(i) for i in v.skipped], "gone": [asdict(i) for i in v.gone],
    }


def cutoff_summary(p: CutoffProbe) -> dict:
    return {"provider": p.provider, "model": p.model, "profile": p.profile, "question_set": p.question_set,
            "claimed": p.claimed, "cutoff": p.cutoff, "last_known": p.last_known, "probed_at": p.probed_at}


def _probe(p: CutoffProbe) -> dict:
    """A probe with its month scores and every question, reply and mark."""
    return {**cutoff_summary(p),
            "months": [{"month": s.month, "right": s.right, "total": s.total, "known": s.known} for s in p.months],
            "answers": [{"event_id": a.event_id, "day": a.day, "question": a.question, "expected": a.expected,
                         "reply": a.reply, "right": a.right} for a in p.answers]}


def replay_cutoff(rc: ReplayCutoff) -> dict:
    """The stages a replay runs on, each distinct model with its probe, and the cutoff they allow together."""
    return {"stages": [{"stage": s, "profile": name} for s, name in rc.stages.items()],
            "models": [{"profile": name, **asdict(rc.facts[name]), "cutoff": rc.cutoff_of(name),
                        "probe": p and _probe(p)}
                       for name, p in rc.probes.items()],
            "cutoff": rc.cutoff, "set_by": rc.set_by, "replay_from": rc.replay_from, "unprobed": list(rc.unprobed),
            "note": rc.note}


def _probes(probes: list[CutoffProbe]) -> dict:
    return {"probes": [cutoff_summary(p) for p in probes]}


def line_step(s: LineStep) -> dict:
    """A regime line's step: what it read and decided, its tilt and any lag warning."""
    run = s.run
    if run is None:
        return {"account": s.account, "off": off_note(s.account), "day": None, "mood": None, "flags": [], "decisions": [],
                "tilt": None, "lag": None}
    return {"account": s.account, "off": None, "day": run.day, "mood": run.mood, "flags": list(run.flags),
            "decisions": [asdict(d) for d in run.decisions], "tilt": run.tilt and asdict(run.tilt), "lag": run.lag}


def _tick_queue(result) -> dict | None:
    """The tick queue runs ticks and the regime lines' steps."""
    return line_step(result) if isinstance(result, LineStep) else tick(result)


# What a finished job's button shows, by kind; research shows its report on the company page.
# A backtest's is its summary: the page fetches the whole run, which Activity never needs.
_RESULTS = {"prices": _prices, "analyse": _analysed, "tick": _tick_queue, "universe": asdict, "fx": _fx, "merges": asdict,
            "discover": asdict, "backtest": backtest_summary, "ask": answer, "replay": StudyStatus.to_json,
            "replay_prices": lambda outcomes: {"listings": [asdict(o) for o in outcomes]}, "probe": _probes,
            "history": asdict, "laya": asdict, "edges": asdict, "reader": lambda r: {**asdict(r), "summary": r.summary},
            "industry": lambda r: {**asdict(r), "summary": r.summary},
            "laya_history": lambda r: {**asdict(r), "summary": r.summary, "ms_per_pair": r.ms_per_pair},
            "laya_test": lambda r: laya_test(r), "signal_test": lambda r: signal_test(r),
            "data_check": lambda r: data_check(r)}


def data_check(run: DataCheckRun | None) -> dict | None:
    return None if run is None else {**asdict(run), "total": run.total, "summary": run.summary}


def laya_test(t: LayaTest) -> dict:
    return {**asdict(t), "standing": t.standing}


def _result(j: Job) -> dict | None:
    """None for a job an earlier server ran: only its outcome was kept, not its result."""
    shape = _RESULTS.get(j.kind)
    return shape(j.result) if shape and j.result is not None else None


def web_link(url: str) -> str | None:
    """A link only for a web address: stored-data items have none, and nothing else may run."""
    return url if urlsplit(url).scheme in ("http", "https") else None


def headline(e: DiscoveryEvidence) -> dict:
    """An article behind a discovery, a link or a shortlist line."""
    return {**asdict(e), "link": web_link(e.url)}


def schedule(view: ScheduleView) -> dict:
    return asdict(view)


def upcoming(up: Upcoming | None) -> dict | None:
    return None if up is None else {"due": up.due, "at": up.at, "postponed": up.postponed, "kind": up.kind,
                                    "label": KIND_LABELS[up.kind]}


def running_tick(j: Job | None) -> dict | None:
    """A tick or midday pass queued or running here, for the banner whose Stop is Activity's cancel."""
    return None if j is None else {"id": j.id, "label": j.label, "state": j.state, "stage": j.progress()}


def order_outcome(order: Order, status: str, note: str | None) -> dict:
    return {"symbol": order.symbol, "account": order.account, "side": order.side, "status": status, "note": note}


def research_summary(run: ResearchRun) -> dict:
    """A history row: enough to pick a report without loading its trace."""
    v = run.report.verdict
    return {"id": run.id, "day": run.report.day, "as_of": run.as_of, "status": run.status,
            "started_at": run.started_at, "model_label": run.model_label,
            "verdict": v and {"label": v.label, "confidence": v.confidence}, "no_verdict": run.report.no_verdict}


def research_run(run: ResearchRun | None, failed: ResearchRun | None = None) -> dict:
    """`failed`: a newer run that failed, shown beside the last report rather than instead of it."""
    if run is None:
        return {"run": None, "failed": None}
    report = run.report
    return {"failed": failed and {"id": failed.id, "status": failed.status, "error": failed.error,
                                  "started_at": failed.started_at}, "run": {
        "id": run.id, "symbol": run.symbol, "status": run.status, "backend": run.backend, "model": run.model,
        "fell_back_to": list(run.fell_back_to), "model_label": run.model_label,
        "stages": [asdict(s) for s in run.stages], "mixed": run.mixed,
        "started_at": run.started_at, "finished_at": run.finished_at, "as_of": run.as_of, "error": run.error,
        "findings": run.findings, "queries": list(run.queries), "badge": UNVALIDATED,
        "report": None if report is None else {
            "day": report.day, "bull": report.bull and asdict(report.bull), "bear": report.bear and asdict(report.bear),
            "findings": [asdict(c) for c in report.findings], "risks": [asdict(c) for c in report.risks],
            "invalidated_if": report.invalidated_if, "rejections": report.rejections,
            "verdict": report.verdict and asdict(report.verdict), "no_verdict": report.no_verdict,
            "did_not_read": report.did_not_read,
        },
        "evidence": [{**asdict(e), "link": web_link(e.url)}
                     for e in run.evidence],
        "steps": [asdict(s) for s in run.steps],
    }}


def coverage(c: Coverage) -> dict:
    """The history coverage table: one row per company, a cell per quarter."""
    run = c.last_run
    return {"start": c.start, "quarters": list(c.quarters), "windows_done": c.windows_done,
            "windows_left": c.windows_left, "caveat": c.caveat, "thin_below": THIN, "noisy_below": NOISY,
            "last_run": {**asdict(run), "summary": run.summary} if run else None,
            "rows": [{"symbol": r.symbol, "name": r.name, "main": r.main, "of": list(r.of), "first_bar": r.first_bar,
                      "thin": list(r.thin), "noisy": list(r.noisy), "search": asdict(r.search),
                      "cells": {q: asdict(x) for q, x in r.cells.items()}} for r in c.rows]}


def signal_test(t: SignalTest) -> dict:
    """A kept test with each verdict as the page shows it (a FAIL too small to see says so)."""
    def windows(ws):
        return [{**asdict(w), "verdict_text": w.verdict_text} for w in ws]
    return {**asdict(t), "train": windows(t.train), "confirm": windows(t.confirm), "verdict_text": t.verdict_text}


def signal_list(signals, results: dict[str, SignalTest], trains: dict[str, SignalTest]) -> dict:
    """The registry's signals, each with its newest full and train-only results (None before a run)."""
    return {"signals": [{"name": s.name, "label": s.label, "horizons": list(s.horizons), "model": s.model,
                         "result": signal_test(results[s.name]) if s.name in results else None,
                         "train_result": signal_test(trains[s.name]) if s.name in trains else None}
                        for s in signals]}


def xbrl_coverage(c) -> dict:
    """The SEC fundamentals' coverage: one row per year, members without a CIK or facts, the last fetch."""
    run = c.last_run
    return {"priced": c.priced, "with_cik": c.with_cik, "with_facts": c.with_facts,
            "without_cik": list(c.without_cik), "without_facts": list(c.without_facts),
            "tickers_fetched_at": c.tickers_fetched_at, "years": [asdict(y) for y in c.years],
            "last_run": {**asdict(run), "summary": run.summary} if run else None}


def insider_coverage(c) -> dict:
    """Form 4 trades and typed filings per year, quarters stored, the last fetch."""
    run = c.last_run
    return {"priced": c.priced, "issuers": c.issuers, "no_cik": c.no_cik, "filings_fetched": c.filings_fetched,
            "quarters": c.quarters, "years": [{"year": y, **row} for y, row in c.years.items()],
            "late": [asdict(x) for x in c.late],
            "last_run": {**asdict(run), "summary": run.summary} if run else None}


def event_study(s) -> dict | None:
    """A kept typed-event study, as stored."""
    return asdict(s) if s is not None else None


def trend_test(t: TrendTest | None) -> dict | None:
    """A kept trend overlay test, each window's two lines in the compare chart's shape."""
    if t is None:
        return None
    return {**asdict(t), "choose": _trend_window(t.choose), "confirm": _trend_window(t.confirm),
            "beside": [_trend_window(w) for w in t.beside]}


def _trend_window(w: TrendWindow) -> dict:
    def line(l: TrendLine, trades: int) -> dict:
        return {**asdict(l), "return": l.ret, "trades": trades}

    return {k: v for k, v in asdict(w).items() if k not in ("trend", "held")} | {
        "lines": [line(w.trend, w.switches), line(w.held, 0)]}


def fund_comparison(c: FundComparison | None) -> dict | None:
    """A kept fund comparison, each fund a line in the compare chart's shape, labelled a proxy."""
    if c is None:
        return None
    return {**asdict(c), "rows": [{**asdict(r), "name": r.key, "label": f"{r.label} (proxy)", "return": r.ret,
                                   "trades": 0} for r in c.rows]}


def testbed_coverage(c: TestbedCoverage) -> dict:
    """The signal testbed's coverage: one row per year, and the members never priced."""
    run, snap = c.last_run, c.snapshot
    return {"start": c.start, "members": c.members, "fetched": c.fetched, "sectors": c.sectors, "calendar": c.calendar,
            "missing_share": c.missing_share, "missing_share_mapped": c.missing_share_mapped, "caveat": c.caveat,
            "aliases": [asdict(a) for a in c.aliases], "renames_checked": c.renames_checked,
            "snapshot": asdict(snap) if snap else None,
            "last_run": {**asdict(run), "summary": run.summary} if run else None,
            "years": [{**asdict(y), "missing_share": y.missing_share, "missing_share_mapped": y.missing_share_mapped}
                      for y in c.years],
            "under_priced": [asdict(u) for u in c.under_priced], "under_priced_below": UNDER_PRICED,
            "duplicates": [asdict(d) for d in c.duplicates], "as_of": c.as_of}

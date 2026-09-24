"""JSON shapes more than one route returns, so every tab reads them the same way."""

from __future__ import annotations

from dataclasses import asdict
from urllib.parse import urlsplit

from oracle.services.activity_service import Job
from oracle.services.analysis_service import WatchedAnalysis
from oracle.services.backtest_service import KeptBacktest
from oracle.services.discovery_chat_service import AnswerView
from oracle.services.discovery_service import DiscoveryEvidence
from oracle.services.fx_service import FxUpdate, credits
from oracle.services.paper_service import Line, Order
from oracle.services.research_service import UNVALIDATED, ResearchRun
from oracle.services.schedule_service import ScheduleView, Upcoming
from oracle.services.tick_service import KIND_LABELS, TickRun


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
            "trades": [asdict(t) for t in b.trades], "notes": list(b.notes)}


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


# What a finished job's button shows, by kind; research shows its report on the company page.
# A backtest's is its summary: the page fetches the whole run, which Activity never needs.
_RESULTS = {"prices": _prices, "analyse": _analysed, "tick": tick, "universe": asdict, "fx": _fx, "merges": asdict,
            "discover": asdict, "backtest": backtest_summary, "ask": answer,
            "replay_prices": lambda outcomes: {"listings": [asdict(o) for o in outcomes]}}


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

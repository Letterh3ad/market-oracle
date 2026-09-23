"""JSON shapes more than one route returns, so every tab reads them the same way."""

from __future__ import annotations

from dataclasses import asdict
from urllib.parse import urlsplit

from oracle.services.activity_service import Job
from oracle.services.analysis_service import WatchedAnalysis
from oracle.services.paper_service import Order
from oracle.services.research_service import UNVALIDATED, ResearchRun
from oracle.services.schedule_service import ScheduleView
from oracle.services.tick_service import TickRun


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
        "trigger": run.trigger, "started_at": run.started_at, "finished_at": run.finished_at,
        "outcome": run.outcome, "first_error": run.first_error, "note": run.note,
        "stages": [asdict(s) for s in run.stages],
    }


def job(j: Job) -> dict:
    return {
        "id": j.id, "kind": j.kind, "label": j.label, "subject": j.subject, "link": j.link, "state": j.state,
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


# What a finished job's button shows, by kind; research shows its report on the company page.
_RESULTS = {"prices": _prices, "analyse": _analysed, "tick": tick}


def _result(j: Job) -> dict | None:
    """None for a job an earlier server ran: only its outcome was kept, not its result."""
    shape = _RESULTS.get(j.kind)
    return shape(j.result) if shape and j.result is not None else None


def schedule(view: ScheduleView) -> dict:
    return asdict(view)


def order_outcome(order: Order, status: str, note: str | None) -> dict:
    return {"symbol": order.symbol, "account": order.account, "side": order.side, "status": status, "note": note}


def research_run(run: ResearchRun | None, failed: ResearchRun | None = None) -> dict:
    """`failed`: a newer run that failed, shown beside the last report rather than instead of it."""
    if run is None:
        return {"run": None, "failed": None}
    report = run.report
    return {"failed": failed and {"id": failed.id, "status": failed.status, "error": failed.error,
                                  "started_at": failed.started_at}, "run": {
        "id": run.id, "symbol": run.symbol, "status": run.status, "backend": run.backend, "model": run.model,
        "fell_back_to": list(run.fell_back_to), "model_label": run.model_label,
        "started_at": run.started_at, "finished_at": run.finished_at, "as_of": run.as_of, "error": run.error,
        "findings": run.findings, "queries": list(run.queries), "badge": UNVALIDATED,
        "report": None if report is None else {
            "day": report.day, "bull": report.bull and asdict(report.bull), "bear": report.bear and asdict(report.bear),
            "findings": [asdict(c) for c in report.findings], "risks": [asdict(c) for c in report.risks],
            "invalidated_if": report.invalidated_if,
        },
        # A link only for a web address: stored-data items have none, and nothing else may run.
        "evidence": [{**asdict(e), "link": e.url if urlsplit(e.url).scheme in ("http", "https") else None}
                     for e in run.evidence],
        "steps": [asdict(s) for s in run.steps],
    }}

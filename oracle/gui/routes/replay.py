"""The replay study's card on the Ledgers tab. The pacer in the server submits the replays;
these only change what it is allowed to do."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, StrictInt, StrictStr

from oracle.gui.deps import get_ctx, get_runner
from oracle.gui.routes import shapes
from oracle.services.activity_service import JobRunner, submit_replay_prices
from oracle.services.context import AppContext
from oracle.services.replay_score import replay_report
from oracle.services.replay_service import (
    measured_cutoff, pause_study, retry_failed, start_study, study_status, submit_create, submit_replace,
)

router = APIRouter(prefix="/api/replay")


@router.get("")
async def status(ctx: AppContext = Depends(get_ctx)) -> dict:
    """Cheap enough for a headless daily check: counts, what runs, failures, the mean replay
    time, and the pacer's last poll (an old one means no server is pacing it)."""
    s = study_status(ctx)
    cutoff = measured_cutoff(ctx)
    return {"study": s.to_json() if s else None, "cutoff": cutoff.isoformat() if cutoff else None}


class Create(BaseModel):
    model_config = ConfigDict(extra="forbid")
    first: date | None = None
    """None: from the model's measured cutoff."""
    symbols: list[StrictStr] | None = None
    """None: picked as a spread."""


@router.post("", status_code=202)
async def create(body: Create, ctx: AppContext = Depends(get_ctx), runner: JobRunner = Depends(get_runner)) -> dict:
    """A job: each listing's prices are checked (and fetched) before the study is saved."""
    return shapes.job(submit_create(ctx, runner, body.first, [s.strip() for s in body.symbols or [] if s.strip()]))


class Replace(BaseModel):
    model_config = ConfigDict(extra="forbid")
    old: StrictStr
    new: StrictStr


@router.post("/replace", status_code=202)
async def replace(body: Replace, ctx: AppContext = Depends(get_ctx),
                  runner: JobRunner = Depends(get_runner)) -> dict:
    """Before the first replay only; the new listing is checked like a picked one."""
    return shapes.job(submit_replace(ctx, runner, body.old.strip(), body.new.strip()))


@router.post("/start")
async def start(ctx: AppContext = Depends(get_ctx)) -> dict:
    return start_study(ctx).to_json()


@router.post("/pause")
async def pause(ctx: AppContext = Depends(get_ctx)) -> dict:
    return pause_study(ctx).to_json()


class Retry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cell_id: StrictInt | None = None
    """None: every failed cell."""


@router.post("/retry")
async def retry(body: Retry, ctx: AppContext = Depends(get_ctx)) -> dict:
    return {"moved": retry_failed(ctx, body.cell_id)}


@router.get("/report")
async def report(ctx: AppContext = Depends(get_ctx)) -> dict:
    """The full score from stored bars; it never fetches, so the card can ask on every change."""
    score = replay_report(ctx)
    return {"report": score.to_json() if score else None}


@router.post("/prices", status_code=202)
async def prices(ctx: AppContext = Depends(get_ctx), runner: JobRunner = Depends(get_runner)) -> dict:
    """Fetch the bars the score lacks, as a job."""
    return shapes.job(submit_replay_prices(ctx, runner))

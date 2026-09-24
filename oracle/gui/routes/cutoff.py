"""The measured cutoff of the models a replay runs on, which replays never start before. A probe
starts as a job (`POST /api/jobs/cutoff-probe`)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from oracle.gui.deps import get_ctx
from oracle.gui.routes import shapes
from oracle.services.context import AppContext
from oracle.services.cutoff_service import VERSION, replay_cutoff

router = APIRouter(prefix="/api/replay/cutoff")


@router.get("")
async def cutoff(model: str | None = None, ctx: AppContext = Depends(get_ctx)) -> dict:
    """Each model a replay on the slots (or every stage on `model`) runs on, with its stored probe
    (null until one has run), and the cutoff they allow together."""
    return {**shapes.replay_cutoff(replay_cutoff(ctx, model or None)), "question_set": VERSION}

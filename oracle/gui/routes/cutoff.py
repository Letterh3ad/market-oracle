"""The model's measured cutoff, which replays never start before. A probe starts as a job
(`POST /api/jobs/cutoff-probe`)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from oracle.gui.deps import get_ctx
from oracle.gui.routes import shapes
from oracle.services.context import AppContext
from oracle.services.cutoff_service import (
    CLAIMED_CUTOFF, VERSION, no_replay_room, probe_profile, profile_model, stored_probe,
)

router = APIRouter(prefix="/api/replay/cutoff")


@router.get("")
async def cutoff(model: str | None = None, ctx: AppContext = Depends(get_ctx)) -> dict:
    """The stored probe of the research slot's model (or `model`'s), null until one has run."""
    name = probe_profile(ctx, model or None)
    provider, model_id = profile_model(ctx, name)
    probe = stored_probe(ctx, name)
    return {"profile": name, "provider": provider, "model": model_id, "claimed": CLAIMED_CUTOFF,
            "question_set": VERSION, "probe": probe and shapes.cutoff(probe, no_replay_room(ctx, probe))}

"""The newest data-check run (Ledgers tab, Data health card). The checks run as a job
(`POST /api/jobs/data-check`), and nightly after the scheduled tick."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from oracle.gui.deps import get_ctx
from oracle.gui.routes import shapes
from oracle.services.context import AppContext
from oracle.services.data_check_service import last_data_check

router = APIRouter(prefix="/api/data")


@router.get("/checks")
async def checks(ctx: AppContext = Depends(get_ctx)) -> dict:
    d = ctx.config.data_checks
    return {"run": shapes.data_check(last_data_check(ctx)), "nightly": d.nightly, "explain": d.explain}

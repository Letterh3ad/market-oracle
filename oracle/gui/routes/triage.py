"""The Research tab's funnel stages (research-lab 05): OSAP's data status and import, a method's
OSAP signal, triage and power runs, and the stage table. The download is a job
(`POST /api/jobs/osap-fetch`). Runs made here are the user's."""

from __future__ import annotations

import asyncio
from dataclasses import asdict

from fastapi import APIRouter, Depends
from pydantic import BaseModel, StrictInt

from oracle.gui.deps import get_ctx
from oracle.services import triage_service as triage
from oracle.services.context import AppContext

router = APIRouter(prefix="/api/lab")
USER = "user"


@router.get("/osap")
async def get_osap(ctx: AppContext = Depends(get_ctx)) -> dict:
    return asdict(await asyncio.to_thread(triage.osap_status, ctx))


class ImportBody(BaseModel):
    folder: str


@router.post("/osap/import")
async def post_import(body: ImportBody, ctx: AppContext = Depends(get_ctx)) -> dict:
    """The pinned OSAP files already in a folder on this PC."""
    return {"imports": [asdict(i) for i in await asyncio.to_thread(triage.import_osap, ctx, body.folder)]}


class MapBody(BaseModel):
    signal: str | None = None


@router.put("/methods/{ref}/osap")
async def put_map(ref: str, body: MapBody, ctx: AppContext = Depends(get_ctx)) -> dict:
    return {"ref": ref, "signal": await asyncio.to_thread(triage.map_method, ctx, ref, body.signal, USER)}


class TriageBody(BaseModel):
    by_hand: str | None = None
    note: str = ""


@router.post("/methods/{ref}/triage")
async def post_triage(ref: str, body: TriageBody, ctx: AppContext = Depends(get_ctx)) -> dict:
    return asdict(await asyncio.to_thread(triage.run_triage, ctx, ref, USER, body.by_hand, body.note))


class PowerBody(BaseModel):
    kind: str = "signal"
    horizon: StrictInt = 20
    expected: float | None = None
    tracking_error: float | None = None
    source: str = ""


@router.post("/methods/{ref}/power")
async def post_power(ref: str, body: PowerBody, ctx: AppContext = Depends(get_ctx)) -> dict:
    return asdict(await asyncio.to_thread(triage.run_power, ctx, ref, body.kind, body.horizon, body.expected,
                                          body.tracking_error, triage.SIGMA_CS, body.source, USER))


@router.get("/table")
async def get_table(ctx: AppContext = Depends(get_ctx)) -> dict:
    return {"rows": [asdict(r) for r in await asyncio.to_thread(triage.stage_table, ctx)], "triage_t": triage.TRIAGE_T}

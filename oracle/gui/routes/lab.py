"""The research lab's literature index: lookup, the method list, one method, marks, recording a source and
the search log. Each call opens the research database on a thread of its own. Marks and records made
here are the user's."""

from __future__ import annotations

import asyncio
from dataclasses import asdict

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from oracle.gui.deps import get_ctx
from oracle.services import lab_service as lab
from oracle.services.context import AppContext

router = APIRouter(prefix="/api/lab")
USER = "user"


class Mark(BaseModel):
    how: str
    note: str = ""


class Record(BaseModel):
    id: str | None = None
    title: str | None = None
    authors: str | None = None
    year: int | None = None
    quality: str | None = None
    note: str | None = None
    read_on: str | None = None
    methods: list[str] = []
    new_method: str | None = None
    family: str = ""
    definition: str = ""
    aliases: list[str] = []


class SearchLog(BaseModel):
    query: str
    where: str
    found: list[str] = []
    note: str = ""


@router.get("/lookup")
async def lookup(q: str, ctx: AppContext = Depends(get_ctx)) -> dict:
    return asdict(await asyncio.to_thread(lab.lookup, ctx, q))


@router.get("/methods")
async def methods(status: str | None = None, ctx: AppContext = Depends(get_ctx)) -> dict:
    rows = await asyncio.to_thread(lab.methods, ctx, status or None)
    return {"methods": [asdict(m) for m in rows], "statuses": list(lab.STATUSES), "marks": sorted(lab.MARKS)}


@router.get("/methods/{ref}")
async def method(ref: str, ctx: AppContext = Depends(get_ctx)) -> dict:
    return asdict(await asyncio.to_thread(lab.method, ctx, ref))


@router.post("/methods/{ref}/mark")
async def mark(ref: str, body: Mark, ctx: AppContext = Depends(get_ctx)) -> dict:
    return asdict(await asyncio.to_thread(lab.mark, ctx, ref, body.how, USER, body.note))


@router.post("/sources")
async def record(body: Record, ctx: AppContext = Depends(get_ctx)) -> dict:
    fields = body.model_dump()
    ident = fields.pop("id")
    return asdict(await asyncio.to_thread(lambda: lab.record(ctx, ident, by=USER, **fields)))


@router.get("/searches")
async def searches(ctx: AppContext = Depends(get_ctx)) -> dict:
    return {"searches": [asdict(s) for s in await asyncio.to_thread(lab.searches, ctx)]}


@router.post("/searches")
async def log_search(body: SearchLog, ctx: AppContext = Depends(get_ctx)) -> dict:
    return asdict(await asyncio.to_thread(lab.log_search, ctx, body.query, body.where, body.found, body.note, USER))

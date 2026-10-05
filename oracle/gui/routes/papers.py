"""The Research tab's papers card (research-lab 07): papers I'd like, the open-access fetch and the inbox scan.
Actions taken here are the user's."""

from __future__ import annotations

import asyncio
from dataclasses import asdict

from fastapi import APIRouter, Depends
from pydantic import BaseModel, StrictStr

from oracle.gui.deps import get_ctx
from oracle.services import papers_service as papers
from oracle.services.context import AppContext

router = APIRouter(prefix="/api/lab/papers")
USER = "user"


class SourceRef(BaseModel):
    source: StrictStr


class Want(SourceRef):
    note: StrictStr = ""


@router.get("/wanted")
async def wanted(ctx: AppContext = Depends(get_ctx)) -> dict:
    rows = await asyncio.to_thread(papers.wanted, ctx)
    return {"wanted": [asdict(w) for w in rows], "inbox": str(papers.inbox_folder(ctx))}


@router.post("/wanted")
async def want(body: Want, ctx: AppContext = Depends(get_ctx)) -> dict:
    return asdict(await asyncio.to_thread(papers.want, ctx, body.source, body.note, USER))


@router.delete("/wanted")
async def unwant(source: str, ctx: AppContext = Depends(get_ctx)) -> dict:
    await asyncio.to_thread(papers.unwant, ctx, source)
    return {"removed": source}


@router.get("/texts")
async def texts(ctx: AppContext = Depends(get_ctx)) -> dict:
    return {"texts": [asdict(h) for h in await asyncio.to_thread(papers.texts, ctx)]}


@router.get("/text")
async def text(source: str, ctx: AppContext = Depends(get_ctx)) -> dict:
    return asdict(await asyncio.to_thread(papers.text, ctx, source))


@router.post("/fetch")
async def fetch(body: SourceRef, ctx: AppContext = Depends(get_ctx)) -> dict:
    # On the event loop: the gate's pacing uses the live database connection, which belongs to this thread. The
    # service parses the PDF in a worker thread.
    return asdict(await papers.fetch(ctx, body.source, USER))


@router.post("/inbox")
async def scan(ctx: AppContext = Depends(get_ctx)) -> dict:
    return asdict(await asyncio.to_thread(papers.scan_inbox, ctx, USER))

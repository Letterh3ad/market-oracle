from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from oracle.gui.deps import get_ctx
from oracle.services.context import AppContext
from oracle.services.tags_service import add_tag, remove_tag

router = APIRouter(prefix="/api/companies")


class NewTag(BaseModel):
    tag: str


@router.post("/{symbol}/tags")
async def tag_company(symbol: str, body: NewTag, ctx: AppContext = Depends(get_ctx)) -> dict:
    return asdict(add_tag(ctx, symbol, body.tag))


@router.delete("/{symbol}/tags/{tag}")
async def untag_company(symbol: str, tag: str, ctx: AppContext = Depends(get_ctx)) -> dict:
    return {"removed": remove_tag(ctx, symbol, tag)}

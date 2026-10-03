"""The Research tab's scoreboard and test registry (research-lab 01)."""

from __future__ import annotations

import asyncio
from dataclasses import asdict

from fastapi import APIRouter, Depends
from pydantic import BaseModel, StrictInt, StrictStr

from oracle.gui.deps import get_ctx
from oracle.services.context import AppContext
from oracle.services.lab_registry import Template, commit_test, drop_test, register_test, registration
from oracle.services.lab_service import bar_text, scoreboard

router = APIRouter(prefix="/api/lab")


@router.get("/scoreboard")
async def get_scoreboard(ctx: AppContext = Depends(get_ctx)) -> dict:
    return asdict(scoreboard(ctx))


@router.get("/tests/{key}")
async def get_test(key: str, ctx: AppContext = Depends(get_ctx)) -> dict:
    r = registration(ctx, key)
    return {**asdict(r), "bar_text": bar_text(r)}


class TemplateBody(BaseModel):
    key: StrictStr
    label: StrictStr
    kind: StrictStr
    horizons: list[StrictInt]
    rule: StrictStr
    universe: StrictStr
    expected_sign: StrictStr
    expected_size: StrictStr
    source: StrictStr
    power: StrictStr
    costs: StrictStr
    kill_stage: StrictStr
    parent: StrictStr | None = None
    different_because: StrictStr | None = None


@router.post("/tests")
async def post_test(body: TemplateBody, ctx: AppContext = Depends(get_ctx)) -> dict:
    data = body.model_dump()
    # On a thread: the near-FAIL check may wait on Ollama's embeddings.
    t = Template(**{**data, "horizons": tuple(data["horizons"])})
    return asdict(await asyncio.to_thread(register_test, ctx, t))


class CommitBody(BaseModel):
    hash: StrictStr


@router.put("/tests/{key}/commit")
async def put_commit(key: str, body: CommitBody, ctx: AppContext = Depends(get_ctx)) -> dict:
    return asdict(await asyncio.to_thread(commit_test, ctx, key, body.hash))


@router.delete("/tests/{key}")
async def delete_test(key: str, ctx: AppContext = Depends(get_ctx)) -> dict:
    drop_test(ctx, key)
    return {"dropped": key}

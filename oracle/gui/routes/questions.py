"""Discovery chat: saved questions and their answers. Asking and rerunning are jobs
(`/api/jobs/ask`)."""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends

from oracle.gui.deps import get_ctx
from oracle.gui.routes import shapes
from oracle.services.context import AppContext
from oracle.services.discovery_chat_service import add_to_discovered, question, questions

router = APIRouter(prefix="/api/questions")


@router.get("")
async def listing(ctx: AppContext = Depends(get_ctx)) -> dict:
    """Every question, newest first, with its newest answer's outcome."""
    return {"questions": [{
        **asdict(line.question), "answers": line.answers,
        "last": line.last and {"id": line.last.id, "status": line.last.status, "started_at": line.last.started_at,
                               "shortlist": len(line.last.shortlist)},
    } for line in questions(ctx)]}


@router.get("/{question_id}")
async def show(question_id: int, ctx: AppContext = Depends(get_ctx)) -> dict:
    """The question with what it refines and its follow-ups, and every answer, newest first."""
    v = question(ctx, question_id)
    return {**asdict(v.question), "asked": v.asked, "chain": [asdict(q) for q in v.chain],
            "follow_ups": [asdict(q) for q in v.follow_ups], "answers": [shapes.answer(a) for a in v.answers]}


@router.post("/answers/{answer_id}/items/{n}/discovered")
async def discovered(answer_id: int, n: int, ctx: AppContext = Depends(get_ctx)) -> dict:
    """A shortlisted company onto the discovered list, the question kept as why."""
    return asdict(await add_to_discovered(ctx, answer_id, n))

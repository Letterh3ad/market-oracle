"""Where the next pound goes: the ISA, workplace pension and Lifetime ISA comparison, and its numbers."""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends
from pydantic import BaseModel, StrictBool, StrictFloat, StrictInt

from oracle.gui.deps import get_ctx
from oracle.services.context import AppContext
from oracle.services.fees_service import ASSUMED_GROWTH
from oracle.services.wrappers_service import save_wrappers, wrapper_comparison

router = APIRouter(prefix="/api/plan")

Number = StrictFloat | StrictInt | None


class Wrappers(BaseModel):
    lisa: StrictBool | None = None
    salary: Number = None
    employee_pct: Number = None
    employer_pct: Number = None
    on_salary: StrictBool | None = None
    sacrifice: StrictBool | None = None
    born: StrictInt | None = None
    needed_before: Number = None
    retire_tax: Number = None
    pension_fee: Number = None
    pension_charge: Number = None


@router.get("/wrappers")
async def wrappers(growth: float = ASSUMED_GROWTH * 100, ctx: AppContext = Depends(get_ctx)) -> dict:
    """`growth` in % a year."""
    return asdict(wrapper_comparison(ctx, growth / 100))


@router.put("/wrappers")
async def put_wrappers(body: Wrappers, ctx: AppContext = Depends(get_ctx)) -> dict:
    """[wrappers] keys in one checked write; a key left out keeps its value."""
    return asdict(save_wrappers(ctx, body.model_dump(exclude_none=True)))

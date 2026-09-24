from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends
from pydantic import BaseModel, StrictFloat, StrictInt, StrictStr

from oracle.gui.deps import get_ctx
from oracle.services.context import AppContext
from oracle.services.models_service import (
    delete_model_profile, model_profile_rows, provider_defaults, save_model_profile, try_model_profile,
)

router = APIRouter(prefix="/api/models")


class Profile(BaseModel):
    provider: StrictStr
    model: StrictStr
    base_url: StrictStr | None = None
    context: StrictInt | None = None
    timeout_s: StrictInt | StrictFloat | None = None
    claimed_cutoff: StrictStr | None = None
    """YYYY-MM-DD; None: the default."""
    key_env: StrictStr | None = None
    key: StrictStr | None = None
    """None keeps the saved key, "" removes it."""


class NewProfile(Profile):
    name: StrictStr


def _listing(ctx: AppContext) -> dict:
    return {"profiles": model_profile_rows(ctx), "providers": provider_defaults(ctx)}


@router.get("")
async def get_models(ctx: AppContext = Depends(get_ctx)) -> dict:
    return _listing(ctx)


@router.post("")
async def post_model(body: NewProfile, ctx: AppContext = Depends(get_ctx)) -> dict:
    save_model_profile(ctx, body.name, body.model_dump(exclude={"name"}), create=True)
    return _listing(ctx)


@router.put("/{name}")
async def put_model(name: str, body: Profile, ctx: AppContext = Depends(get_ctx)) -> dict:
    save_model_profile(ctx, name, body.model_dump(), create=False)
    return _listing(ctx)


@router.delete("/{name}")
async def delete_model(name: str, ctx: AppContext = Depends(get_ctx)) -> dict:
    delete_model_profile(ctx, name)
    return _listing(ctx)


@router.post("/{name}/test")
async def post_model_test(name: str, ctx: AppContext = Depends(get_ctx)) -> dict:
    """Waits for the reply, up to the profile's timeout."""
    return asdict(await try_model_profile(ctx, name))

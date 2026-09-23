from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from oracle.gui.errors import install_error_handlers
from oracle.gui.routes import status
from oracle.services.context import AppContext

STATIC_DIR = Path(__file__).parent / "static"


def build_app(ctx: AppContext) -> FastAPI:
    app = FastAPI(title="Market Oracle")
    app.state.ctx = ctx
    install_error_handlers(app)
    app.include_router(status.router)
    # Under a prefix, not a catch-all at "/", which would shadow the API.
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    return app

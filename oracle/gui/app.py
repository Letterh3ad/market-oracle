from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from oracle.gui.errors import install_error_handlers
from oracle.gui.routes import companies, jobs, status, universe, watchlists
from oracle.services.context import AppContext

STATIC_DIR = Path(__file__).parent / "static"
# Revalidate every load (a cheap 304 when unchanged), so an updated module is never run
# from a stale cache beside fresh ones.
NO_CACHE = {"Cache-Control": "no-cache"}


class _Static(StaticFiles):
    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers.update(NO_CACHE)
        return response


def build_app(ctx: AppContext) -> FastAPI:
    app = FastAPI(title="Market Oracle")
    app.state.ctx = ctx
    install_error_handlers(app)
    app.include_router(status.router)
    app.include_router(universe.router)
    app.include_router(companies.router)
    app.include_router(watchlists.router)
    app.include_router(jobs.router)
    # Under a prefix, not a catch-all at "/", which would shadow the API.
    app.mount("/static", _Static(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html", headers=NO_CACHE)

    return app

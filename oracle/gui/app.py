from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from oracle.gui.errors import install_error_handlers
from oracle.gui.routes import (
    activity, backtests, companies, cutoff, discovery, factors, fx, history, holiday as holiday_routes, jobs, ledgers,
    models, plan, questions, reader, replay, schedule as tick_schedule, settings, status, testbed, universe, watchlists,
)
from oracle.gui.routes import control as app_control
from oracle.gui.routes import laya as laya_routes
from oracle.services.activity_service import JobRunner, sweep_leftovers
from oracle.services.app_service import AppControl, server_launch
from oracle.services.context import AppContext
from oracle.services.holiday_service import Holiday
from oracle.services.job_executor import ThreadExecutor
from oracle.services.replay_service import ReplayPacer
from oracle.services.schedule_service import Scheduler

STATIC_DIR = Path(__file__).parent / "static"
# Revalidate every load (a cheap 304 when unchanged), so an updated module is never run
# from a stale cache beside fresh ones.
NO_CACHE = {"Cache-Control": "no-cache"}


class _Static(StaticFiles):
    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers.update(NO_CACHE)
        return response


def build_app(ctx: AppContext, schedule: bool = False, control: AppControl | None = None,
              holiday: Holiday | None = None) -> FastAPI:
    """With `schedule`, the scheduler and the replay pacer poll for as long as the server does
    (tests leave them off, though postpone, skip and the replay card still answer). Jobs still
    running when the server stops are interrupted. `control` restarts the server; by default
    it starts this server's own command. `holiday` arms the holiday watch; by default the real one."""
    runner = JobRunner(ctx, ThreadExecutor(ctx))
    scheduler = Scheduler(ctx, runner=runner)
    pacer = ReplayPacer(ctx, runner, scheduler)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        sweep_leftovers(ctx)
        tasks = [asyncio.create_task(scheduler.run()), asyncio.create_task(pacer.run())] if schedule else []
        app.state.scheduler_task = tasks[0] if tasks else None
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            await runner.stop()

    app = FastAPI(title="Market Oracle", lifespan=lifespan)
    app.state.ctx = ctx
    app.state.runner = runner
    app.state.scheduler = scheduler
    app.state.pacer = pacer
    app.state.control = control or AppControl(server_launch(ctx))
    app.state.holiday = holiday
    install_error_handlers(app)
    app.include_router(status.router)
    app.include_router(activity.router)
    app.include_router(app_control.router)
    app.include_router(universe.router)
    app.include_router(fx.router)
    app.include_router(companies.router)
    app.include_router(watchlists.router)
    app.include_router(factors.router)
    app.include_router(discovery.router)
    app.include_router(questions.router)
    app.include_router(jobs.router)
    app.include_router(settings.router)
    app.include_router(models.router)
    app.include_router(tick_schedule.router)
    app.include_router(ledgers.router)
    app.include_router(backtests.router)
    app.include_router(history.router)
    app.include_router(testbed.router)
    app.include_router(plan.router)
    app.include_router(reader.router)
    app.include_router(laya_routes.router)
    app.include_router(replay.router)
    app.include_router(cutoff.router)
    app.include_router(holiday_routes.router)
    # Under a prefix, not a catch-all at "/", which would shadow the API.
    app.mount("/static", _Static(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html", headers=NO_CACHE)

    return app

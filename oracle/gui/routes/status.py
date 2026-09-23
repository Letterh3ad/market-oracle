from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends

from oracle.gui.deps import get_control, get_ctx
from oracle.gui.routes import shapes
from oracle.services.app_service import AppControl
from oracle.services.context import AppContext
from oracle.services.net_service import source_statuses
from oracle.services.schedule_service import schedule_status
from oracle.services.status_service import status
from oracle.services.tick_service import last_tick
from oracle.services.universe_service import universe_brief

router = APIRouter(prefix="/api")


# Every route is `async def`: sync routes run in a thread pool, and the SQLite connection
# belongs to the event loop's thread.
@router.get("/status")
async def get_status(ctx: AppContext = Depends(get_ctx), control: AppControl = Depends(get_control)) -> dict:
    universe = universe_brief(ctx)
    return {
        # Which server answered, so a restarting page can tell the new one from the old.
        "server": {"pid": control.pid, "started_at": _iso(control.started_at), "version": control.version,
                   "version_on_disk": control.version_on_disk()},
        "base_currency": status(ctx).base_currency,
        "universe": {
            "loaded": universe.loaded,
            "listings": universe.listings,
            "refreshed_at": _iso(universe.refreshed_at),
            "next": None if universe.loaded else "run `oracle universe refresh`",
        },
        "automation": {"enabled": ctx.config.automation.enabled},
        "tick": shapes.tick(last_tick(ctx)),
        "schedule": shapes.schedule(schedule_status(ctx)),
        "sources": [
            {
                "source": s.source,
                "state": s.state,
                "rate_per_second": s.rate_per_second,
                "reason": s.reason,
                "paused_until": _iso(datetime.fromtimestamp(s.open_until, timezone.utc))
                if s.open_until is not None else None,
                "requests_last_hour": s.requests_last_hour,
            }
            for s in source_statuses(ctx)
        ],
    }


def _iso(ts: datetime | None) -> str | None:
    return ts.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if ts else None

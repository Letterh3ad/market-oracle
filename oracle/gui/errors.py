from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from oracle.services.errors import (
    CannotRestart, ConfigInUse, InvalidQuery, InvalidTag, InvalidWatchlistName, JobAlreadyRunning, ModelUnavailable, NoPrices, PriceUnavailable,
    SearchFailed, ServiceError,
    UniverseNotLoaded, UniverseUnavailable, UnknownBacktest, UnknownFilterValue, UnknownJob, UnknownQuestion, UnknownResearchRun,
    UnknownSymbol,
    UnknownCorrection, UnknownLink, UnknownModelProfile, UnknownWatchlist,
)

log = logging.getLogger(__name__)

# Most specific first: the first matching class wins. Anything else a service raises is 400.
STATUS = (
    ((UnknownSymbol, UnknownWatchlist, UnknownJob, UnknownResearchRun, UnknownBacktest, UnknownQuestion, UnknownLink,
      UnknownModelProfile, UnknownCorrection),
     404),
    ((InvalidQuery, UnknownFilterValue, InvalidTag, InvalidWatchlistName), 400),
    ((JobAlreadyRunning, UniverseNotLoaded, NoPrices, CannotRestart, ConfigInUse), 409),
    ((PriceUnavailable, UniverseUnavailable, SearchFailed), 502),
    # The local model is a service of this machine, not an upstream: unavailable, not a bad gateway.
    ((ModelUnavailable,), 503),
)
UNEXPECTED = "something went wrong inside Market Oracle: try again, and if it keeps happening press Restart"


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ServiceError)
    async def _service_error(request: Request, exc: ServiceError) -> JSONResponse:
        code = next((c for kinds, c in STATUS if isinstance(exc, kinds)), 400)
        return JSONResponse(status_code=code, content={"error": str(exc)})

    @app.exception_handler(RequestValidationError)
    async def _malformed(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Named by the field alone (not "query.limit"), as the page's forms name them.
        first = exc.errors()[0]
        return JSONResponse(status_code=400, content={"error": f"{first['loc'][-1]}: {first['msg']}"})

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error on %s", request.url.path, exc_info=exc)
        return JSONResponse(status_code=500, content={"error": UNEXPECTED})

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from oracle.services.errors import (
    InvalidQuery, InvalidTag, InvalidWatchlistName, JobAlreadyRunning, NoPrices, PriceUnavailable, ServiceError,
    UniverseNotLoaded, UniverseUnavailable, UnknownFilterValue, UnknownSymbol, UnknownWatchlist,
)

log = logging.getLogger(__name__)

# Most specific first: the first matching class wins. Anything else a service raises is 400.
STATUS = (
    ((UnknownSymbol, UnknownWatchlist), 404),
    ((InvalidQuery, UnknownFilterValue, InvalidTag, InvalidWatchlistName), 400),
    ((JobAlreadyRunning, UniverseNotLoaded, NoPrices), 409),
    ((PriceUnavailable, UniverseUnavailable), 502),
)
UNEXPECTED = "something went wrong inside Market Oracle: see the terminal running `oracle gui`"


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ServiceError)
    async def _service_error(request: Request, exc: ServiceError) -> JSONResponse:
        code = next((c for kinds, c in STATUS if isinstance(exc, kinds)), 400)
        return JSONResponse(status_code=code, content={"error": str(exc)})

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error on %s", request.url.path, exc_info=exc)
        return JSONResponse(status_code=500, content={"error": UNEXPECTED})

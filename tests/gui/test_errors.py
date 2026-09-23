import httpx
import pytest

from oracle.gui.app import build_app
from oracle.services.errors import (
    InvalidQuery, JobAlreadyRunning, NoPrices, PriceUnavailable, ServiceError, UniverseNotLoaded, UnknownSymbol,
)

CASES = [
    (UnknownSymbol("no listing 'XYZ' in the universe"), 404),
    (InvalidQuery("a buy must spend more than nothing"), 400),
    (JobAlreadyRunning("prices update is already running"), 409),
    (UniverseNotLoaded(), 409),
    (NoPrices("no prices stored for AAPL"), 409),
    (PriceUnavailable("yahoo is not answering"), 502),
    (ServiceError("anything else a service reports"), 400),
]


def raising(ctx, exc):
    app = build_app(ctx)

    @app.get("/api/boom")
    async def boom():
        raise exc

    # Unhandled errors still answer 500; the transport must not re-raise them into the test.
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://oracle.test")


@pytest.mark.parametrize(("exc", "code"), CASES)
async def test_service_errors_carry_their_message_and_a_fitting_status(ctx, exc, code):
    async with raising(ctx, exc) as client:
        response = await client.get("/api/boom")
    assert (response.status_code, response.json()) == (code, {"error": str(exc)})


async def test_an_unexpected_error_is_a_plain_500_without_the_traceback(ctx):
    async with raising(ctx, KeyError("secret internals")) as client:
        response = await client.get("/api/boom")
    assert response.status_code == 500
    assert response.json() == {"error": "something went wrong inside Market Oracle: see the terminal running `oracle gui`"}


async def test_a_malformed_request_says_which_field_is_wrong(client):
    response = await client.get("/api/universe/search", params={"limit": 0})
    assert response.status_code == 400
    assert response.json() == {"error": "limit: Input should be greater than or equal to 1"}

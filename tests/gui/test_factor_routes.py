from oracle.store.watchlists import WatchlistRepo
from tests.gui.conftest import NOW
from tests.services.test_price_factors import rising, store


async def test_the_table_and_one_company_rank_across_the_set(ctx, client):
    for s, rate in (("UP", 0.001), ("FLAT", 0.0)):
        store(ctx, s, rising(rate))
        WatchlistRepo(ctx.conn).add("main", s, "2026-09-01T00:00:00Z")
    store(ctx, "OUT", rising(0.002))

    body = (await client.get("/api/factors", params={"list": "main"})).json()
    assert body["as_of"] == NOW.date().isoformat() and body["watchlist"] == "main"
    assert [(r["symbol"], r["momentum_pct"]) for r in body["rows"]] == [("UP", 1.0), ("FLAT", 0.0)]
    assert all(r["as_of"] <= body["as_of"] for r in body["rows"])

    earlier = (await client.get("/api/factors", params={"as_of": "2026-06-01"})).json()
    assert earlier["as_of"] == "2026-06-01"

    out = (await client.get("/api/factors/OUT")).json()
    assert out["momentum_pct"] == 1.0 and out["in_set"] is False and out["set_size"] == 2


async def test_a_future_day_or_unknown_list_is_refused(client):
    assert (await client.get("/api/factors", params={"as_of": "2030-01-01"})).status_code == 400
    assert (await client.get("/api/factors", params={"list": "nope"})).status_code == 404

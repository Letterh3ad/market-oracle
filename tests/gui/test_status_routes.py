from oracle.net.gate import SourceGate


async def test_a_fresh_database_says_the_universe_needs_loading(client):
    body = (await client.get("/api/status")).json()
    assert body["universe"] == {"loaded": False, "listings": 0, "refreshed_at": None,
                                "next": "run `oracle universe refresh`"}
    assert body["base_currency"] == "GBP"


async def test_each_source_reports_its_health_and_a_tripped_breaker_says_why(ctx, client):
    gate = SourceGate(ctx.conn, ctx.clock, ctx.config.sources)
    for _ in range(3):
        gate.record("yahoo", 429)
    sources = {s["source"]: s for s in (await client.get("/api/status")).json()["sources"]}
    assert sources["ecb"]["state"] == "ok"
    assert sources["yahoo"]["state"] == "paused"
    assert "429" in sources["yahoo"]["reason"] and sources["yahoo"]["paused_until"].endswith("Z")

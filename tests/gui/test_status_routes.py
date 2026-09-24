from oracle.net.gate import SourceGate


async def test_a_fresh_database_says_the_universe_needs_loading(client):
    body = (await client.get("/api/status")).json()
    assert body["universe"] == {"loaded": False, "listings": 0, "refreshed_at": None,
                                "next": "press Refresh universe on the Universe tab"}
    assert body["base_currency"] == "GBP"


async def test_each_source_reports_its_health_and_a_tripped_breaker_says_why(ctx, client):
    gate = SourceGate(ctx.conn, ctx.clock, ctx.config.sources)
    for _ in range(3):
        gate.record("yahoo", 429)
    sources = {s["source"]: s for s in (await client.get("/api/status")).json()["sources"]}
    assert (sources["ecb"]["state"], sources["ecb"]["rate_per_second"]) == ("ok", 0.5)
    assert sources["yahoo"]["state"] == "paused"
    assert "429" in sources["yahoo"]["reason"] and sources["yahoo"]["paused_until"].endswith("Z")


async def test_a_loaded_universe_reports_its_listings_and_when_it_was_refreshed(ctx, loaded):
    from oracle.services.universe_service import universe_status

    universe = (await loaded.get("/api/status")).json()["universe"]
    assert universe["loaded"] and universe["next"] is None
    full = universe_status(ctx)
    assert universe["listings"] == full.listings
    assert universe["refreshed_at"] == full.refreshed_at.isoformat().replace("+00:00", "Z")


async def test_the_about_block_says_what_oracle_status_prints(ctx, client, capsys):
    from oracle.cli import status as status_cli

    status_cli.run(ctx, None)
    printed = dict(line.split(None, 1) for line in capsys.readouterr().out.splitlines()[1:])
    about = (await client.get("/api/status")).json()["about"]
    assert about == {
        "schema": int(printed["schema"]), "database": printed["database"], "config": None,
        "currency": printed["currency"], "created": printed["created"], "version": printed["version"],
    }
    assert printed["config"].startswith("defaults")

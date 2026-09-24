import asyncio
import threading

import oracle.gui.routes.universe as universe_routes

from tests.gui.jobs import run_job, wait_job

# Fake universe by market cap band (tests/services/test_universe_filters.py):
#   Mega AAPL, 005930.KS, KO; Large T, 7203.T; Mid X.TO; Small APLE; none QQQ.L, NONAME.F, SSNLF
BY_SIZE = ["AAPL", "005930.KS", "KO", "T", "7203.T", "X.TO", "APLE", "QQQ.L", "NONAME.F", "SSNLF"]


async def symbols(client, **params):
    body = (await client.get("/api/universe/search", params=params)).json()
    return [h["symbol"] for h in body["hits"]], body["more"]


async def test_browsing_pages_biggest_first_and_says_when_there_is_more(loaded):
    assert await symbols(loaded, limit=3) == (BY_SIZE[:3], True)
    assert await symbols(loaded, limit=3, offset=9) == (BY_SIZE[9:], False)


async def test_market_cap_sorts_either_way_with_filters_repeated_for_either(loaded):
    assert (await symbols(loaded, sort="market_cap", desc="true"))[0] == BY_SIZE
    assert (await symbols(loaded, sort="market_cap"))[0][:2] == ["APLE", "X.TO"]
    assert (await symbols(loaded, country=["Japan", "Canada"]))[0] == ["7203.T", "X.TO"]


async def test_a_hit_carries_what_the_table_shows(loaded):
    body = (await loaded.get("/api/universe/search", params={"q": "apple", "limit": 1})).json()
    hit = body["hits"][0]
    assert (hit["symbol"], hit["name"], hit["market_cap"], hit["tags"]) == ("AAPL", "Apple Inc.", "Mega Cap", [])


async def test_a_bad_filter_value_says_what_was_meant(loaded):
    response = await loaded.get("/api/universe/search", params={"country": "Germny"})
    assert response.status_code == 400 and "Germany" in response.json()["error"]


async def test_searching_before_the_universe_is_loaded_says_how_to_load_it(client):
    response = await client.get("/api/universe/search")
    assert response.status_code == 409 and "refresh the universe" in response.json()["error"]


async def test_options_name_every_filter_and_sort(client):
    body = (await client.get("/api/universe/options")).json()
    assert "country" in body["filters"] and "market_cap" in body["filters"]
    assert body["sorts"][:3] == ["relevance", "name", "symbol"]


async def test_facets_list_values_with_company_counts(loaded):
    rows = (await loaded.get("/api/universe/facets/country")).json()
    assert rows[:2] == [{"value": "United States", "companies": 4, "label": None},
                        {"value": "South Korea", "companies": 2, "label": None}]
    assert (await loaded.get("/api/universe/facets/colour")).status_code == 400


async def test_a_tag_added_from_a_result_filters_the_search_and_can_be_removed(loaded):
    added = (await loaded.post("/api/companies/aapl/tags", json={"tag": "  My   Picks "})).json()
    assert added == {"tag": "my picks", "symbol": "AAPL", "name": "Apple Inc."}
    assert (await symbols(loaded, tag="my picks"))[0] == ["AAPL"]
    assert (await loaded.delete("/api/companies/AAPL/tags/my picks")).json() == {"removed": True}
    assert (await loaded.delete("/api/companies/AAPL/tags/my picks")).json() == {"removed": False}


async def test_a_bad_tag_is_refused_with_the_reason(loaded):
    response = await loaded.post("/api/companies/AAPL/tags", json={"tag": ""})
    assert response.status_code == 400 and response.json()["error"]


async def test_a_result_is_watched_once_and_the_lists_count_it(loaded):
    first = (await loaded.post("/api/watchlists/main/items", json={"symbol": "ko"})).json()
    again = (await loaded.post("/api/watchlists/main/items", json={"symbol": "KO"})).json()
    assert (first["symbol"], first["added"], again["added"]) == ("KO", True, False)
    lists = (await loaded.get("/api/watchlists")).json()
    assert [(w["name"], w["items"]) for w in lists] == [("main", 1)]


async def test_status_before_loading_points_at_the_refresh_button(client):
    status = (await client.get("/api/status")).json()["universe"]
    assert "Refresh universe" in status["next"]
    assert (await client.get("/api/universe/status")).json()["loaded"] is False


async def test_refresh_runs_as_a_job_of_its_own_and_shows_what_it_loaded(client):
    job = await run_job(client, "/api/jobs/universe")
    assert (job["kind"], job["state"], job["link"]) == ("universe", "done", "#universe")
    assert job["result"]["loaded"] and job["result"]["listings"] > 0
    status = (await client.get("/api/universe/status")).json()
    assert (status["listings"], status["companies"]) == (job["result"]["listings"], job["result"]["companies"])
    assert status["source_commit"] and status["refreshed_at"] and status["by_type"]["equity"][0] > 0


async def test_a_second_refresh_joins_the_running_one(client, monkeypatch):
    import threading

    from oracle.services import activity_service

    release = threading.Event()  # set from the test, waited on by the job's worker thread

    async def slow(c, on_stage=None):
        await asyncio.to_thread(release.wait)

    monkeypatch.setattr(activity_service, "refresh_universe", slow)
    first = (await client.post("/api/jobs/universe")).json()
    second = (await client.post("/api/jobs/universe")).json()
    release.set()
    assert second["id"] == first["id"]
    assert (await wait_job(client, first))["state"] == "done"


async def test_every_tag_is_listed_with_its_companies(loaded):
    await loaded.post("/api/companies/AAPL/tags", json={"tag": "picks"})
    await loaded.post("/api/companies/KO/tags", json={"tag": "picks"})
    assert (await loaded.get("/api/universe/tags")).json() == [
        {"tag": "picks", "symbols": ["AAPL", "KO"], "orphaned": []}]


async def test_status_counts_off_the_event_loop_on_a_connection_of_its_own(loaded, monkeypatch):
    real, seen = universe_routes.universe_status, []

    def spy(ctx):
        seen.append(threading.get_ident())
        return real(ctx)

    monkeypatch.setattr(universe_routes, "universe_status", spy)
    assert (await loaded.get("/api/universe/status")).json()["loaded"] is True
    assert seen and seen[0] != threading.get_ident()


async def test_proposed_merges_run_as_a_job_that_changes_nothing(ctx, loaded):
    before = ctx.conn.execute("SELECT symbol, company_key, is_primary FROM listings ORDER BY symbol").fetchall()
    job = await run_job(loaded, "/api/jobs/merges")
    assert (job["kind"], job["state"], job["link"]) == ("merges", "done", "#universe")
    assert job["result"] == {"merges": [], "enabled": False, "flag_counts": {"legal forms differ": 0, "no country": 0}}
    after = ctx.conn.execute("SELECT symbol, company_key, is_primary FROM listings ORDER BY symbol").fetchall()
    assert [tuple(r) for r in after] == [tuple(r) for r in before]

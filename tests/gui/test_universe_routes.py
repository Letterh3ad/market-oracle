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
    assert response.status_code == 409 and "oracle universe refresh" in response.json()["error"]


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

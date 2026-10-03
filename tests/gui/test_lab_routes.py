MCLEAN = "https://ivey.uwo.ca/media/3775549/pontiff.pdf"


async def test_lookup_gives_the_nearest_methods_and_sources(client):
    r = (await client.get("/api/lab/lookup", params={"q": "does academic reserch destory stock return predictibility"})).json()
    assert r["key"] is None and r["sources"][0]["source"]["url"] == MCLEAN
    assert len(r["methods"]) == 5 and {"ref", "name", "status", "score", "matched", "rating"} <= r["methods"][0].keys()
    r = (await client.get("/api/lab/lookup", params={"q": "http://ivey.uwo.ca/media/3775549/pontiff.pdf/"})).json()
    assert r["key"] == "url:ivey.uwo.ca/media/3775549/pontiff.pdf" and r["note"] == "exact id match"
    bad = await client.get("/api/lab/lookup", params={"q": " "})
    assert bad.status_code == 400


async def test_methods_list_filter_and_one_method(client):
    every = (await client.get("/api/lab/methods")).json()
    assert len(every["methods"]) == 205 and "new" in every["statuses"] and every["marks"] == ["dig", "drop", "go"]
    built = (await client.get("/api/lab/methods", params={"status": "built"})).json()["methods"]
    assert [m["ref"] for m in built] == ["X-13"]
    one = (await client.get("/api/lab/methods/T-15")).json()
    assert one["name"] == "52-week-high proximity" and one["sources"][0]["keys"] == [
        "url:academicnewsletter.sufe.edu.cn/info/361244"]
    assert (await client.get("/api/lab/methods/T-999")).status_code == 404
    assert (await client.get("/api/lab/methods", params={"status": "maybe"})).status_code == 400


async def test_a_mark_from_the_page_is_the_users(client):
    r = await client.post("/api/lab/methods/T-15/mark", json={"how": "go", "note": "cheap"})
    m = r.json()
    assert r.status_code == 200 and m["status"] == "go" and m["status_by"] == "user"
    assert m["marks"][0] == {"mark": "go", "status_from": "new", "status_to": "go", "by": "user",
                             "at": m["status_at"], "note": "cheap"}
    refused = await client.post("/api/lab/methods/X-01/mark", json={"how": "go"})
    assert refused.status_code == 400 and "never re-tested" in refused.json()["error"]


async def test_record_a_source_with_a_new_method(client):
    r = await client.post("/api/lab/sources", json={
        "id": "arXiv:2601.99999", "title": "Fresh paper", "quality": "working-paper", "methods": ["T-15"],
        "new_method": "Fresh idea", "definition": "Buy X.", "aliases": ["fresh"], "read_on": "2026-10-01"})
    body = r.json()
    assert r.status_code == 200 and body["created"] and body["method"]["ref"] == "M-001"
    assert body["source"]["methods"] == ["T-15", "M-001"] and body["source"]["added_by"] == "user"
    assert len(body["nearest"]) == 5
    bad = await client.post("/api/lab/sources", json={"id": "nonsense"})
    assert bad.status_code == 400 and "not a DOI" in bad.json()["error"]


async def test_searches_are_logged_and_listed(client):
    r = await client.post("/api/lab/searches", json={"query": "decay", "where": "SSRN", "found": [MCLEAN], "note": "x"})
    assert r.status_code == 200 and r.json()["found"][0]["url"] == MCLEAN
    listed = (await client.get("/api/lab/searches")).json()["searches"]
    assert [(s["query"], s["place"], s["by"]) for s in listed] == [("decay", "SSRN", "user")]
    bad = await client.post("/api/lab/searches", json={"query": "decay", "where": ""})
    assert bad.status_code == 400


async def test_a_blank_title_is_refused_and_never_blanks_a_known_one(client):
    bad = await client.post("/api/lab/sources", json={"title": "  "})
    assert bad.status_code == 400 and "nothing to record" in bad.json()["error"]
    kept = (await client.post("/api/lab/sources", json={"id": MCLEAN, "title": " ", "note": "again"})).json()
    assert kept["source"]["title"] == "Does Academic Research Destroy Stock Return Predictability?"
    assert (await client.get("/api/lab/methods/T-10")).json()["archive_note"].startswith("Tried-other")

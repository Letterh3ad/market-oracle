from datetime import datetime, timezone

from oracle.research.news import Article
from oracle.services.links_service import LinkFound, record_links
from oracle.store.articles import ArticleRepo


async def test_the_map_lists_each_related_company_with_why_when_and_what_it_is_doing(loaded, ctx):
    [a] = ArticleRepo(ctx.conn).save([Article("https://news.test/1", "Toyota and Apple talk", "Wire",
                                              datetime(2026, 9, 22, tzinfo=timezone.utc))], "google_news",
                                     "2026-09-23T00:00:00Z")
    record_links(ctx, [LinkFound("7203.T", "AAPL", "partner", (a,))], "discovery")
    body = (await loaded.get("/api/companies/TM/industry")).json()
    assert body["symbol"] == "7203.T" and body["relations"] == ["supplier", "customer", "competitor", "partner"]
    [apple] = body["related"]
    assert (apple["symbol"], apple["name"], apple["link_id"] is not None) == ("AAPL", "Apple Inc.", True)
    [rel] = apple["relations"]
    assert (rel["kind"], rel["why"], rel["source"]) == ("partner", "a partner", "discovery")
    assert apple["first_known"] == rel["first_known"] and rel["first_known"].startswith("2026-09-23")
    assert [e["link"] for e in apple["evidence"]] == ["https://news.test/1"]
    assert apple["now"] == {"last_close": None, "move_5d": None, "move_20d": None, "last_earnings": None,
                            "next_earnings": None, "headlines": []}


async def test_a_link_is_added_and_deleted_from_the_panel(loaded):
    added = await loaded.post("/api/companies/7203.T/links", json={"other": "KO", "relation": "customer"})
    assert added.status_code == 200 and added.json()["added"]["symbol"] == "KO"
    [ko] = (await loaded.get("/api/companies/7203.T/industry")).json()["related"]
    assert ko["relations"][0]["source"] == "user" and ko["relations"][0]["why"] == "a customer"
    gone = await loaded.delete(f"/api/companies/7203.T/links/{ko['link_id']}")
    assert gone.status_code == 200
    assert (await loaded.get("/api/companies/7203.T/industry")).json()["related"] == []


async def test_a_bad_link_is_refused(loaded):
    bad = await loaded.post("/api/companies/7203.T/links", json={"other": "KO", "relation": "friend"})
    assert bad.status_code == 400 and "one of supplier" in bad.json()["error"]
    assert (await loaded.post("/api/companies/7203.T/links", json={"other": "NOPE", "relation": "partner"})
            ).status_code == 404

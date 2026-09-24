from datetime import datetime, timezone

from oracle.research.news import Article
from oracle.services.links_service import LinkFound, record_links
from oracle.store.articles import ArticleRepo


async def test_a_company_lists_its_links_with_evidence_and_a_delete_keeps_them_gone(loaded, ctx):
    [a] = ArticleRepo(ctx.conn).save([Article("https://news.test/1", "Toyota and Apple talk", "Wire",
                                              datetime(2026, 9, 22, tzinfo=timezone.utc))], "google_news",
                                     "2026-09-23T00:00:00Z")
    record_links(ctx, [LinkFound("7203.T", "AAPL", "partner", (a,)), LinkFound("7203.T", "KO", "peer")], "discovery")
    [first, second] = (await loaded.get("/api/companies/TM/links")).json()["links"]
    assert (first["symbol"], first["name"], first["relation"], first["why"], first["source"]) == (
        "AAPL", "Apple Inc.", "partner", "a partner", "discovery")
    [e] = first["evidence"]
    assert (e["title"], e["link"], e["outlet"]) == ("Toyota and Apple talk", "https://news.test/1", "Wire")
    assert (second["symbol"], second["why"], second["evidence"]) == ("KO", "in the same industry", [])

    gone = await loaded.delete(f"/api/companies/7203.T/links/{first['id']}")
    assert gone.status_code == 200 and gone.json()["deleted"]["symbol"] == "AAPL"
    assert [x["symbol"] for x in (await loaded.get("/api/companies/AAPL.MI/links")).json()["links"]] == []
    record_links(ctx, [LinkFound("7203.T", "AAPL", "partner", (a,))], "discovery")
    assert [x["symbol"] for x in (await loaded.get("/api/companies/7203.T/links")).json()["links"]] == ["KO"]


async def test_deleting_a_link_the_company_does_not_have_is_not_found(loaded, ctx):
    record_links(ctx, [LinkFound("7203.T", "KO", "partner")], "discovery")
    [link] = (await loaded.get("/api/companies/KO/links")).json()["links"]
    assert (await loaded.delete(f"/api/companies/AAPL/links/{link['id']}")).status_code == 404

import pytest

from tests.fakes.pdf import paper
from tests.lab.test_papers_service import GKX, GKX_PDF, JT, JT_TITLE, context, web


@pytest.fixture
def ctx(tmp_path):
    with context(tmp_path, web({GKX_PDF: paper("Empirical Asset Pricing via Machine Learning", GKX)})) as c:
        yield c


async def test_fetch_from_the_page_keeps_the_text_and_a_miss_lands_on_the_wanted_list(client):
    r = (await client.post("/api/lab/papers/fetch", json={"source": f"doi:{GKX}"})).json()
    assert r["copy"]["via"] == "unpaywall" and r["pages"] == 2 and r["wanted"] is False
    await client.post("/api/lab/sources", json={"id": JT, "title": JT_TITLE})
    r = (await client.post("/api/lab/papers/fetch", json={"source": JT})).json()
    assert r["copy"] is None and r["wanted"] is True
    listed = (await client.get("/api/lab/papers/wanted")).json()
    assert [w["source"]["title"] for w in listed["wanted"]] == [JT_TITLE] and listed["wanted"][0]["by"] == "user"
    assert listed["inbox"].endswith("inbox")
    bad = await client.post("/api/lab/papers/fetch", json={"source": "10.9999/nothing"})
    assert bad.status_code == 400 and "not a recorded source" in bad.json()["error"]


async def test_want_unwant_and_scan_from_the_page(client, tmp_path):
    await client.post("/api/lab/sources", json={"id": JT, "title": JT_TITLE})
    w = (await client.post("/api/lab/papers/wanted", json={"source": JT, "note": "library"})).json()
    assert w["note"] == "library" and w["reason"] == "added by hand"
    assert (await client.delete("/api/lab/papers/wanted", params={"source": JT})).json() == {"removed": JT}
    assert (await client.delete("/api/lab/papers/wanted", params={"source": JT})).status_code == 400
    inbox = tmp_path / ".scratch" / "research-lab" / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "jt.pdf").write_bytes(paper(JT_TITLE))
    scan = (await client.post("/api/lab/papers/inbox")).json()
    (one,) = scan["results"]
    assert one["file"] == "jt.pdf" and one["source"]["title"] == JT_TITLE and one["matched"].startswith("title ")

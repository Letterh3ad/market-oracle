"""The Research tab's weekly page routes (research-lab 08): build it like `oracle lab page`, and show the last
build's time and path."""

from tests.fakes.results import build


async def test_the_card_builds_the_page_and_shows_the_last_build(ctx, client, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    build(ctx, monkeypatch)
    before = (await client.get("/api/lab/page")).json()
    assert before["last"] is None and before["default"].endswith("page.html")
    assert before["capabilities"]["db"]["rules"] == [{"path": "marks", "read": "view", "write": "interact"}]
    made = (await client.post("/api/lab/page", json={"questions": ["Widen to mid caps?", ""]})).json()
    assert made["questions"] == 1 and made["rows"] == 10 and made["at"] == "2026-09-23T12:00:00+00:00"
    page = tmp_path / ".scratch" / "research-lab" / "weekly" / "page.html"
    assert made["path"] == str(page.resolve()) and "Widen to mid caps?" in page.read_text(encoding="utf-8")
    assert (await client.get("/api/lab/page")).json()["last"] == made
    bad = await client.post("/api/lab/page", json={"questions": [1]})
    assert bad.status_code in (400, 422)

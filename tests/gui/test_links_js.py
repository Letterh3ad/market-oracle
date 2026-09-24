import json
import subprocess

import pytest

from oracle.gui.app import STATIC_DIR
from tests.gui.test_research_js import NODE, PRELUDE

pytestmark = pytest.mark.skipif(NODE is None, reason="needs node to run the page's modules")

LINK = {"id": 7, "symbol": "6902.T", "name": "DENSO Corporation", "why": "a supplier", "last_seen": "2026-09-24T12:00:00Z",
        "evidence": [{"title": "Denso to supply Toyota", "link": "https://news.test/1", "outlet": "Wire",
                      "published_at": "2026-09-23T08:00:00+00:00"},
                     {"title": "Bad link", "link": "javascript:alert(1)", "outlet": None, "published_at": None}]}


def run(module: str, body: str):
    script = f"""
        {PRELUDE}
        globalThis.window = {{ scrollX: 0, scrollY: 0, scrollTo: () => {{}} }};
        const m = await import({json.dumps((STATIC_DIR / "lib" / module).as_uri())});
        {body}
    """
    out = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def test_each_link_shows_why_its_dated_headlines_and_a_delete_that_reloads():
    out = run("links.js", f"""
        let changes = 0;
        const table = m.linksTable("7203.T", [{json.dumps(LINK)}], () => changes++);
        const links = table.all((n) => n.tag === "a").map((a) => a.attrs.href);
        button(table, "Delete").fire("click");
        await settle();
        console.log(JSON.stringify({{ text: table.textContent, links, asked, changes }}));
    """)
    assert "a supplier" in out["text"] and "2026-09-23 Denso to supply Toyota (Wire)" in out["text"]
    assert "undated Bad link" in out["text"] and "2026-09-24" in out["text"]
    assert out["links"] == ["#company/6902.T", "https://news.test/1"], "only a web address becomes a link"
    assert out["asked"] == ["DELETE /api/companies/7203.T/links/7"] and out["changes"] == 1


def test_no_links_says_where_they_come_from():
    out = run("links.js", 'console.log(JSON.stringify(m.linksTable("KO", [], () => {}).textContent));')
    assert out.startswith("No linked companies yet.") and "strongest two" in out


def test_evidence_found_by_a_links_search_says_which_link():
    item = {"id": 3, "title": "Microsoft and Apple sign AI deal", "link": "https://x.com/1", "outlet": "X",
            "published": "2026-09-20T12:00:00+00:00", "query": "Microsoft Apple", "via": "Microsoft (MSFT), a partner",
            "facts": [], "read": None, "body": None}
    out = run("research.js", f"console.log(JSON.stringify(m.evidenceItem({json.dumps(item)}).textContent));")
    assert out.endswith('(found by "Microsoft Apple") via the link to Microsoft (MSFT), a partner')

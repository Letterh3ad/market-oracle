import json
import subprocess

import pytest

from oracle.gui.app import STATIC_DIR
from tests.gui.test_research_js import NODE, PRELUDE

pytestmark = pytest.mark.skipif(NODE is None, reason="needs node to run the page's modules")

EVIDENCE = [{"title": "Denso to supply Toyota", "link": "https://news.test/1", "outlet": "Wire",
             "published_at": "2026-09-23T08:00:00+00:00"},
            {"title": "Bad link", "link": "javascript:alert(1)", "outlet": None, "published_at": None}]
NOTHING = {"last_close": None, "move_5d": None, "move_20d": None, "last_earnings": None, "next_earnings": None,
           "headlines": []}
MAP = {
    "symbol": "7203.T", "name": "Toyota", "basis": "industry Automobiles", "note": None,
    "relations": ["supplier", "customer", "competitor", "partner"],
    "related": [
        {"symbol": "6902.T", "name": "DENSO Corporation", "market_cap": "Large Cap", "link_id": 7,
         "first_known": None,
         "relations": [{"kind": "supplier", "why": "a supplier", "source": "discovery",
                        "first_known": "2026-09-24T12:00:00+00:00"},
                       {"kind": "competitor", "why": "a peer (industry group Autos)", "source": "peers",
                        "first_known": None}],
         "evidence": EVIDENCE,
         "now": {"last_close": "2026-09-29", "move_5d": 0.021, "move_20d": -0.05, "last_earnings": "2026-08-05",
                 "next_earnings": None,
                 "headlines": [{"title": "Denso lifts outlook", "link": "https://news.test/2", "outlet": "Nikkei",
                                "published_at": "2026-09-28T01:00:00+00:00"}]}},
        {"symbol": "HMC", "name": "Honda", "market_cap": None, "link_id": None, "first_known": None,
         "relations": [{"kind": "competitor", "why": "same industry (Automobiles)", "source": "industry",
                        "first_known": None}],
         "evidence": [], "now": NOTHING},
    ],
}


def run(module: str, body: str):
    script = f"""
        {PRELUDE}
        globalThis.window = {{ scrollX: 0, scrollY: 0, scrollTo: () => {{}} }};
        const m = await import({json.dumps((STATIC_DIR / "lib" / module).as_uri())});
        {body}
    """
    out = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def test_each_related_company_shows_how_since_when_what_it_is_doing_and_a_delete_on_a_link():
    out = run("industry.js", f"""
        let changes = 0;
        const panel = m.industryPanel("7203.T", {json.dumps(MAP)}, () => changes++);
        const links = panel.all((n) => n.tag === "a").map((a) => a.attrs.href);
        const deletes = panel.all((n) => n.tag === "button" && n.textContent === "Delete").length;
        button(panel, "Delete").fire("click");
        await settle();
        console.log(JSON.stringify({{ text: panel.textContent, links, deletes, asked, changes }}));
    """)
    text = out["text"]
    assert "Competitors by industry Automobiles." in text
    assert "a supplier (discovery, first known 2026-09-24)" in text
    assert "a peer (industry group Autos) (peers, static field)" in text
    assert "2026-09-23 Denso to supply Toyota (Wire)" in text and "undated Bad link" in text
    assert "5d +2.10% 20d -5.00% to 2026-09-29" in text
    assert "last earnings 2026-08-05, next earnings not known" in text
    assert "2026-09-28 Denso lifts outlook (Nikkei)" in text
    assert "5d n/a 20d n/a (no stored prices)" in text and "no stored headlines" in text
    assert "https://news.test/1" in out["links"] and "javascript:alert(1)" not in out["links"]
    assert out["deletes"] == 1, "a competitor from static fields has nothing to delete"
    assert out["asked"] == ["DELETE /api/companies/7203.T/links/7"] and out["changes"] == 1


def test_add_link_posts_the_symbol_and_relation_then_reloads():
    out = run("industry.js", f"""
        let changes = 0;
        const panel = m.industryPanel("7203.T", {json.dumps({**MAP, "related": []})}, () => changes++);
        const [input] = panel.all((n) => n.tag === "input");
        const [pick] = panel.all((n) => n.tag === "select");
        const options = pick.children.map((o) => o.textContent);
        input.value = " KO ";
        pick.value = "customer";
        const sent = [];
        const real = globalThis.fetch;
        globalThis.fetch = async (url, options) => {{ sent.push(options.body); return real(url, options); }};
        button(panel, "Add link").fire("click");
        await settle();
        console.log(JSON.stringify({{ text: panel.textContent, options, asked, sent, changes }}));
    """)
    assert "No related companies yet." in out["text"]
    assert out["options"] == ["is a supplier of 7203.T", "is a customer of 7203.T", "is a competitor of 7203.T",
                              "is a partner of 7203.T"]
    assert out["asked"] == ["POST /api/companies/7203.T/links"]
    assert json.loads(out["sent"][0]) == {"other": "KO", "relation": "customer"} and out["changes"] == 1


def test_opening_the_panel_asks_only_for_the_stored_map():
    out = run("industry.js", f"""
        ANSWERS["/api/companies/7203.T/industry"] = {json.dumps(MAP)};
        ANSWERS["/api/companies/7203.T/edges"] = {json.dumps({"enabled": True, "edges": [TIE]})};
        const box = Object.assign(new Node("div"), {{ style: {{}}, offsetHeight: 0 }});
        await m.showIndustry(box, "7203.T");
        console.log(JSON.stringify({{ asked, filled: box.textContent.includes("DENSO Corporation"),
                                      ties: box.textContent.includes("BorgWarner orders parts") }}));
    """)
    assert out == {"asked": ["GET /api/companies/7203.T/industry", "GET /api/companies/7203.T/edges"],
                   "filled": True, "ties": True}


TIE = {"id": 4, "symbol": "7203.T", "other": "BWA", "other_name": "BorgWarner Inc.", "kind": "news",
       "relation": "customer", "rule": "customer", "choice": "none", "prob": 0.61, "model": "laya", "state": "unconfirmed",
       "evidence": "BorgWarner orders parts from Toyota", "known_at": "2026-09-07T09:00:00+00:00",
       "title": "BorgWarner orders parts from Toyota", "url": "https://news.test/4", "link": "https://news.test/4"}
LINKED = {**TIE, "id": 5, "other": "6902.T", "other_name": "DENSO Corporation", "relation": "supplier", "kind": "filing", "documents": 3,
          "rule": "supplier", "choice": "supplier", "prob": 0.9, "state": "linked", "title": "10-K",
          "evidence": "We rely on DENSO for sensors.", "link": "javascript:alert(1)"}


def test_ties_show_evidence_and_only_unconfirmed_ones_can_be_confirmed_or_deleted():
    out = run("industry.js", f"""
        ANSWERS["/api/companies/7203.T/edges/4/confirm"] = {{}};
        ANSWERS["/api/jobs/edges"] = {{ id: 9, state: "done", result: {{}} }};
        let changes = 0;
        const box = m.tiesSection("7203.T", {json.dumps({"enabled": True, "edges": [TIE, LINKED]})}, () => changes++);
        const buttons = box.all((n) => n.tag === "button").map((b) => b.textContent);
        const links = box.all((n) => n.tag === "a").map((a) => a.attrs.href);
        button(box, "Confirm").fire("click");
        await settle(10);
        button(box, "Find for the main list").fire("click");
        await settle(10);
        console.log(JSON.stringify({{ asked, changes, buttons, links, text: box.textContent }}));
    """)
    assert out["buttons"] == ["Find suppliers and customers", "Find for the main list", "Confirm", "Delete"]
    assert out["asked"] == ["POST /api/companies/7203.T/edges/4/confirm", "POST /api/jobs/edges?watchlist=main"]
    assert out["changes"] == 2
    text = out["text"]
    assert 'a customerunconfirmed"BorgWarner orders parts from Toyota"2026-09-07' in text
    assert "rule customer, Laya none 61%" in text and '"We rely on DENSO for sensors."' in text
    assert "(and 2 later documents)" in text and "later document" not in text.split("We rely")[0]
    assert "uncalibrated" in text
    assert "javascript:alert(1)" not in out["links"]


def test_laya_off_offers_no_find_button():
    out = run("industry.js", 'console.log(JSON.stringify(m.tiesSection("7203.T", { enabled: false, edges: [] }, '
              '() => {}).textContent));')
    assert "Laya is off" in out and "Find suppliers" not in out and "No ties read yet" in out


def test_evidence_found_by_a_links_search_says_which_link():
    item = {"id": 3, "title": "Microsoft and Apple sign AI deal", "link": "https://x.com/1", "outlet": "X",
            "published": "2026-09-20T12:00:00+00:00", "query": "Microsoft Apple", "via": "Microsoft (MSFT), a partner",
            "facts": [], "read": None, "body": None}
    out = run("research.js", f"console.log(JSON.stringify(m.evidenceItem({json.dumps(item)}).textContent));")
    assert out.endswith('(found by "Microsoft Apple") via the link to Microsoft (MSFT), a partner')

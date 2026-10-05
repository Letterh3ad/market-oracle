"""The Research tab's funnel stages card (research-lab 05), run in node against mocked routes; the
last test feeds it the real routes' answers."""

import json
import subprocess

import pytest

from oracle.gui.app import STATIC_DIR
from oracle.services import triage_service as triage
from tests.fakes.osap import write_release
from tests.gui.test_backtest_js import NODE, PRELUDE
from tests.lab.test_triage_service import KEPT

pytestmark = pytest.mark.skipif(NODE is None, reason="needs node to run the page's modules")

STATUS = {"release": "2025.10", "signals": {"Predictor": 212, "Placebo": 114},
          "variants": [{"variant": "large", "signals": 212, "rows": 9, "first": "1926-07", "last": "2024-12"}],
          "imports": [], "folder": "x", "on_disk": [], "window_open": False, "next_window": "Mon 2026-10-05 01:00",
          "window": "the compute window (weekdays 01:00-18:00, weekends 01:00-08:00, local time)"}
TRIAGED = {"stage": "triage", "outcome": "stop", "summary": "OSAP Mom12m: large caps since 2000 +0.30%/month t 1.00",
           "detail": {}, "status_from": "go", "status_to": "fail", "by": "user", "at": "2026-10-04T12:00:00+00:00"}
TABLE = {"rows": [{"ref": "T-13", "name": "Residual momentum", "family": "A", "status": "fail", "signal": "Mom12m",
                   "triage": TRIAGED, "power": None, "runs": 1}], "triage_t": 1.5}


def run(body: str, status=None, table=None):
    script = f"""
        {PRELUDE}
        const bodies = [];
        const inner = globalThis.fetch;
        globalThis.fetch = async (url, options) => {{ if (options?.body) bodies.push(JSON.parse(options.body));
          return inner(url, options); }};
        ANSWERS["GET /api/lab/osap"] = {json.dumps(status or STATUS)};
        ANSWERS["GET /api/lab/table"] = {json.dumps(table or TABLE)};
        ANSWERS["POST /api/lab/methods/T-13/triage"] = {json.dumps(TRIAGED)};
        ANSWERS["PUT /api/lab/methods/T-13/osap"] = {{ ref: "T-13", signal: "Mom12m" }};
        ANSWERS["POST /api/lab/methods/T-13/power"] = {{ status: 400, error: "T-13 is fail: power takes a method that passed triage" }};
        ANSWERS["POST /api/jobs/osap-fetch"] = {{ status: 400, error: "bulk downloads run only in the compute window" }};
        const {{ funnelSection }} = await import({json.dumps((STATIC_DIR / "lib" / "triage.js").as_uri())});
        const section = funnelSection();
        const node = section.node;
        {body}
    """
    out = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, encoding="utf-8", check=True)
    return json.loads(out.stdout)


def test_the_card_shows_the_data_and_the_stage_table():
    text = run("""
        await section.start(); await settle();
        console.log(JSON.stringify(node.textContent));
    """)
    assert "OSAP release 2025.10: 212 Predictor, 114 Placebo. large caps: 212 signals, 1926-07 to 2024-12." in text
    assert "opens Mon 2026-10-05 01:00" in text
    assert "Residual momentum" in text and "stop: OSAP Mom12m: large caps since 2000 +0.30%/month t 1.00" in text
    assert "go to fail, user, 2026-10-04" in text


DETAILED = {**TRIAGED, "detail": {
    "original": {"spans": [{"label": "in sample", "mean": 1.31, "t": 3.74, "n": 300},
                           {"label": "post publication", "mean": 0.5, "t": 1.2, "n": 120},
                           {"label": "empty", "mean": None, "t": None, "n": 0}], "decay": 0.62},
    "large": {"spans": [{"label": "since 2000", "mean": 0.3, "t": None, "n": 300}], "decay": None},
    "paper": {"authors": "Jegadeesh and Titman", "year": 1993, "return": 1.31, "t": 3.74},
    "formula_ic": 0.0156, "expected_from": "given"}}


def test_the_stage_cell_shows_the_cli_detail_lines():
    text = run("""
        await section.start(); await settle();
        console.log(JSON.stringify(node.all((n) => n.attrs["data-detail"] === "line").map((n) => n.textContent)));
    """, table={**TABLE, "rows": [{**TABLE["rows"][0], "triage": DETAILED, "power": DETAILED}]})
    assert text[:3] == [
        "original portfolio: in sample +1.31 t 3.74 (300 m); post publication +0.50 t 1.20 (120 m); decay 62%",
        "large caps: since 2000 +0.30 t 0.00 (300 m)",
        "paper (Jegadeesh and Titman 1993): +1.31%/month t 3.74"]
    assert text[3] == ("names-and-days formula: 0.0156 (it leaves out cross-name correlation); expected from given")


def test_pick_map_triage_and_a_refused_power_and_download():
    asked, bodies, text = run("""
        await section.start(); await settle();
        button(node, "Pick").fire("click");
        labelled(node, "OSAP signal (acronym)").value = "Mom12m";
        asked.length = 0;
        button(node, "Set OSAP signal").fire("click"); await settle();
        labelled(node, "Triage note (the evidence; needed by hand)").value = "a note";
        button(node, "Triage").fire("click"); await settle();
        const after = node.textContent;
        labelled(node, "Expected IC or yearly excess (blank: from OSAP)").value = "0.03";
        button(node, "Check power").fire("click"); await settle();
        const refused = node.textContent;
        button(node, "Download OSAP").fire("click"); await settle();
        console.log(JSON.stringify([asked, bodies, [after, refused, node.textContent]]));
    """)
    assert asked[:2] == ["PUT /api/lab/methods/T-13/osap", "GET /api/lab/osap"]
    assert "POST /api/lab/methods/T-13/triage" in asked and "POST /api/jobs/osap-fetch" in asked
    assert bodies == [{"signal": "Mom12m"}, {"by_hand": None, "note": "a note"},
                      {"kind": "signal", "horizon": 20, "expected": 0.03, "tracking_error": None, "source": ""}]
    after, refused, last = text
    assert "triage stop: OSAP Mom12m: large caps since 2000 +0.30%/month t 1.00 (go to fail)." in after
    assert "power takes a method that passed triage" in refused
    assert "bulk downloads run only in the compute window" in last


async def test_the_card_reads_the_real_routes(client, tmp_path, monkeypatch):
    monkeypatch.setattr(triage, "observed_windows", lambda c: KEPT)
    await client.post("/api/lab/osap/import", json={"folder": str(write_release(tmp_path / "osap"))})
    await client.put("/api/lab/methods/T-13/osap", json={"signal": "Mom12m"})
    await client.post("/api/lab/methods/T-13/mark", json={"how": "go"})
    await client.post("/api/lab/methods/T-13/triage", json={})
    status, table = (await client.get("/api/lab/osap")).json(), (await client.get("/api/lab/table")).json()
    text = run("""
        await section.start(); await settle();
        console.log(JSON.stringify(node.textContent));
    """, status, table)
    assert "OSAP release 2025.10: 1 Placebo, 4 Predictor. large caps: 3 signals, 1990-01 to 2024-12" in text
    assert "stop: OSAP Mom12m: large caps since 2000 +0.30%/month t 1.00 over 300 months" in text

import json
import subprocess

import pytest

from oracle.gui.app import STATIC_DIR
from tests.gui.test_backtest_js import NODE, PRELUDE

pytestmark = pytest.mark.skipif(NODE is None, reason="needs node to run the page's modules")

COVERAGE = {"start": "2010-01-01", "members": 830, "fetched": 830, "sectors": 700, "calendar": "SPY",
            "missing_share": 0.0712, "caveat": "delisted members are mostly not on Yahoo",
            "snapshot": {"source": "fja05680/sp500", "url": "https://x", "revision": "a2430f2af0c7",
                         "fetched_at": "2026-10-01T09:00:00+00:00", "first_day": "1996-01-02",
                         "last_day": "2026-08-18", "changes": 2720},
            "last_run": {"stopped": None, "failures": [], "finished_at": "2026-10-01T10:00:00+00:00",
                         "summary": "finished: 830 members"},
            "years": [{"year": 2010, "members": 520, "member_days": 126000, "with_bars": 100000, "unpriced": 60,
                       "missing_share": 0.2063},
                      {"year": 2025, "members": 510, "member_days": 125000, "with_bars": 124500, "unpriced": 2,
                       "missing_share": 0.004}],
            "under_priced_below": 0.5, "as_of": "2026-10-01",
            "duplicates": [{"tickers": ["KORS", "CPRI"], "first_day": "2016-01-04", "last_day": "2018-09-17",
                            "days": 680, "why": "Michael Kors Holdings listed again as Capri Holdings"}],
            "under_priced": [{"ticker": "ANTM", "first_day": "2010-01-04", "last_day": "2022-06-27",
                              "member_days": 3140, "with_bars": 0, "note": "not on Yahoo"},
                             {"ticker": "EA", "first_day": "2010-01-04", "last_day": "2026-06-01",
                              "member_days": 4170, "with_bars": 1, "note": "priced on 1 of 4170 member-days"}]}


def run(body: str):
    script = f"""
        {PRELUDE}
        ANSWERS["GET /api/testbed/coverage"] = {json.dumps(COVERAGE)};
        ANSWERS["POST /api/jobs/testbed"] = {{ id: 7, kind: "testbed", state: "queued", progress: null }};
        ANSWERS["POST /api/jobs/testbed?refresh=true"] = {{ id: 8, kind: "testbed", state: "queued", progress: null }};
        const {{ signalTestsSection }} = await import({json.dumps((STATIC_DIR / "lib" / "signal_tests.js").as_uri())});
        const section = signalTestsSection();
        const node = section.node;
        {body}
    """
    out = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def test_the_card_shows_the_missing_share_per_year_and_the_members_never_priced():
    text, red = run("""
        section.start(); await settle();
        const red = node.all((n) => n.tag === "td" && n.className.includes("down")).map((n) => n.textContent);
        console.log(JSON.stringify([node.textContent, red]));
    """)
    assert "Membership: fja05680/sp500 at a2430f2, fetched 2026-10-01 09:00 UTC, 2720 rows" in text
    assert "830 members from 2010-01-01, 830 fetched, 700 with a sector" in text and "Missing share 7.1%" in text
    assert "Last build, 2026-10-01 10:00 UTC: finished: 830 members." in text
    assert "126000" in text and "under 50% of their member-days (2)" in text and "not on Yahoo" in text
    assert "priced on 1 of 4170 member-days" in text and "Listed twice: KORS and CPRI" in text
    assert "Its last change is 2026-08-18: to 2026-10-01" in text
    assert red == ["20.6%"]


def test_the_table_reloads_when_the_build_ends():
    asked, text = run("""
        section.start(); await settle();
        ANSWERS["GET /api/activity/7"] = { id: 7, kind: "testbed", state: "done", result: null };
        asked.length = 0;
        button(node, "Build testbed").fire("click"); await settle(1700);
        console.log(JSON.stringify([asked, node.textContent]));
    """)
    assert asked == ["POST /api/jobs/testbed", "GET /api/activity/7", "GET /api/testbed/coverage"]
    assert "The testbed build finished." in text


def test_build_and_refresh_start_the_job_and_say_where_to_follow_it():
    asked, text = run("""
        section.start(); await settle();
        asked.length = 0;
        button(node, "Build testbed").fire("click"); await settle();
        button(node, "Refresh testbed").fire("click"); await settle();
        console.log(JSON.stringify([asked, node.textContent]));
    """)
    assert asked == ["POST /api/jobs/testbed", "POST /api/jobs/testbed?refresh=true"]
    assert "The testbed build is queued" in text and "Activity tab" in text

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



def window(name, start, end, ic, t, power, missing, verdict, positive, counted, text=None):
    return {"window": name, "start": start, "end": end, "horizon": 20, "ic": ic, "t": t, "days": 2300, "names": 470.0,
            "effective_days": 115.0, "power": power, "observed_power": 0.021, "missing_share": missing,
            "verdict": verdict, "passed": verdict == "PASS", "positive": positive, "counted": counted,
            "verdict_text": text or verdict,
            "segments": [{"start": start, "end": "2010-06-30", "ic": None, "t": None, "days": 0, "counted": False,
                          "why": "0 IC days (needs 20)"}]}


UNSEEN = "FAIL (could not see an IC this small: needed 0.0210)"
SIGNALS = {"signals": [
    {"name": "momentum_12_1", "label": "12-1 momentum", "horizons": [20], "model": None, "result": {
        "signal": "momentum_12_1", "label": "12-1 momentum", "horizons": [20], "model": None, "cutoff": None,
        "train": [window("train", "2010-01-01", "2019-12-31", 0.0123, 1.4, 0.0081, 0.2345, "FAIL", 11, 18)],
        "confirm": [window("confirm", "2020-01-01", "2026-09-30", 0.004, 0.3, 0.0102, 0.0512, "FAIL", 6, 13, UNSEEN)],
        "train_verdict": "FAIL", "verdict": "FAIL", "verdict_text": UNSEEN, "passed": False, "train_only": False, "first_day": "2010-01-04",
        "last_day": "2026-09-30", "tickers": 848, "snapshot": "fja05680/sp500 at a2430f2, last change 2026-08-18",
        "run_at": "2026-10-01T12:00:00+00:00", "caveat": "train is survivorship-weighted", "id": 3},
     "train_result": None},
    {"name": "reversal_1m", "label": "1-month reversal", "horizons": [5], "model": None, "result": None,
     "train_result": {"signal": "reversal_1m", "label": "1-month reversal", "horizons": [5], "model": None,
                      "cutoff": None, "train": [window("train", "2010-01-01", "2019-12-31", 0.0078, 1.83, 0.0047,
                                                       0.2620, "FAIL", 14, 20)],
                      "confirm": [], "train_verdict": "FAIL", "verdict": "TRAIN ONLY", "verdict_text": "TRAIN ONLY",
                      "passed": False, "train_only": True, "first_day": "2010-01-04", "last_day": "2019-12-31",
                      "tickers": 700, "snapshot": None, "run_at": "2026-10-01T13:00:00+00:00", "caveat": "c",
                      "id": 4}}]}


XBRL = {"priced": 609, "with_cik": 590, "with_facts": 585, "without_cik": ["OLDCO"], "without_facts": ["BANKX"],
        "tickers_fetched_at": "2026-10-01T10:00:00+00:00", "last_run": None,
        "years": [{"year": 2012, "members": 450, "with_cik": 440, "with_facts": 438,
                   "scored": {"sue": 300, "gross_profitability": 380, "earnings_yield": 420},
                   "events": {"sue": 900, "gross_profitability": 1400, "earnings_yield": 1600}}]}


INSIDERS = {"priced": 609, "issuers": 600, "no_cik": 9, "filings_fetched": 600, "quarters": {"2009q1": 10, "2026q2": 20},
            "last_run": None, "years": [{"year": 2015, "P": 812, "P_issuers": 140, "S": 9100, "4": 30000, "8-K": 6000,
                                         "8-K 2.02": 2300, "8-K 1.01": 700, "8-K 2.01": 90}]}


def row(h, mean, t):
    return {"horizon": h, "n": 50, "days": 40, "mean": mean, "t": t, "positive": 30}


EVENTS = {"full": {"types": [{"name": "2.02", "label": "8-K item 2.02: results", "verdict": "FAIL", "passed": False,
                              "train": [row(h, 0.001, 0.5) for h in (1, 5, 20, 60)],
                              "confirm": [row(h, -0.0123, -1.1) for h in (1, 5, 20, 60)]}],
                   "primary": 20, "horizons": [1, 5, 20, 60], "first_day": "2010-01-04", "last_day": "2026-09-30",
                   "trades_through": "2026-06-30", "missing_train": 0.2, "missing_confirm": 0.05,
                   "run_at": "2026-10-01T12:00:00+00:00", "train_only": False, "caveat": "clustered by day", "id": 5},
          "train": None}


def run(body: str):
    script = f"""
        {PRELUDE}
        ANSWERS["GET /api/testbed/coverage"] = {json.dumps(COVERAGE)};
        ANSWERS["GET /api/signals"] = {json.dumps(SIGNALS)};
        ANSWERS["POST /api/jobs/signal-test"] = {{ id: 9, kind: "signal_test", label: "Signal test: reversal_1m",
          state: "queued", progress: null }};
        ANSWERS["POST /api/jobs/testbed"] = {{ id: 7, kind: "testbed", state: "queued", progress: null }};
        ANSWERS["GET /api/xbrl/coverage"] = {json.dumps(XBRL)};
        ANSWERS["POST /api/jobs/xbrl"] = {{ id: 11, kind: "xbrl", state: "queued", progress: null }};
        ANSWERS["POST /api/jobs/xbrl?refresh=true"] = {{ id: 12, kind: "xbrl", state: "queued", progress: null }};
        ANSWERS["GET /api/insiders/coverage"] = {json.dumps(INSIDERS)};
        ANSWERS["GET /api/events"] = {json.dumps(EVENTS)};
        ANSWERS["POST /api/jobs/insiders"] = {{ id: 13, kind: "insiders", state: "queued", progress: null }};
        ANSWERS["POST /api/jobs/event-study"] = {{ id: 14, kind: "event_study", state: "queued", progress: null }};
        ANSWERS["POST /api/jobs/event-study?train_only=true"] = {{ id: 15, kind: "event_study", state: "queued",
          progress: null }};
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
    assert red == ["20.6%", "FAIL", UNSEEN], "a high missing share and failed verdicts"


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


def test_each_signal_shows_power_missing_share_train_confirm_and_verdict():
    text = run("""
        section.start(); await settle();
        console.log(JSON.stringify(node.textContent));
    """)
    assert "momentum_12_1" in text and "0.0210 / 0.0210" in text and "formula 0.0081 / 0.0102" in text
    assert "23.4% / 5.1%" in text
    assert "IC +0.0123, t 1.40, 11+ of 18 segments: FAIL" in text
    assert f"IC +0.0040, t 0.30, 6+ of 13 segments: {UNSEEN}" in text and text.count(UNSEEN) == 2
    assert "reversal_1m" in text and "Not run yet." in text
    assert "power 0.0210 (formula 0.0102); missing share 5.1%" in text and "0 IC days (needs 20)" in text
    assert "smallest IC the window could see at t 2, from its daily ICs' own spread" in text
    assert "Train only, 2026-10-01 13:00 UTC: IC +0.0078, t 1.83, 14+ of 20 segments: FAIL" in text
    assert "Caveat: train is survivorship-weighted" in text


def test_run_test_starts_the_job_and_reloads_the_table_when_it_ends():
    asked, text = run("""
        section.start(); await settle();
        ANSWERS["GET /api/activity/9"] = { id: 9, kind: "signal_test", state: "done", result: null };
        asked.length = 0;
        node.all((n) => n.tag === "button" && n.textContent === "Run test")[1].fire("click"); await settle(1700);
        console.log(JSON.stringify([asked, node.textContent]));
    """)
    assert asked == ["POST /api/jobs/signal-test?name=reversal_1m", "GET /api/activity/9", "GET /api/signals"]
    assert "The signal test finished." in text


def test_train_only_starts_its_own_job():
    asked = run("""
        section.start(); await settle();
        asked.length = 0;
        node.all((n) => n.tag === "button" && n.textContent === "Train only")[0].fire("click"); await settle();
        console.log(JSON.stringify(asked));
    """)
    assert asked[0] == "POST /api/jobs/signal-test?name=momentum_12_1&train_only=true"


def test_the_fundamentals_coverage_shows_per_year_and_fetch_starts_its_job():
    asked, text = run("""
        section.start(); await settle();
        asked.length = 0;
        button(node, "Fetch SEC fundamentals").fire("click"); await settle();
        button(node, "Refetch").fire("click"); await settle();
        console.log(JSON.stringify([asked, node.textContent]));
    """)
    assert asked == ["POST /api/jobs/xbrl", "POST /api/jobs/xbrl?refresh=true"]
    assert "609 priced members, 590 with a CIK, 585 with facts. No fetch has run yet." in text
    assert "300 / 900" in text and "420 / 1600" in text and "sue: members / filings" in text
    assert "No CIK in SEC's ticker map (1): OLDCO." in text and "No XBRL facts at SEC (1): BANKX." in text
    assert "The fundamentals fetch is queued" in text


def test_form4_coverage_and_the_event_study_tables_with_their_jobs():
    asked, text = run("""
        section.start(); await settle();
        asked.length = 0;
        button(node, "Fetch Form 4s and 8-Ks").fire("click"); await settle();
        button(node, "Run event study").fire("click"); await settle();
        button(node, "Event study, train only").fire("click"); await settle();
        console.log(JSON.stringify([asked, node.textContent]));
    """)
    assert asked == ["POST /api/jobs/insiders", "POST /api/jobs/event-study", "POST /api/jobs/event-study?train_only=true"]
    assert "600 issuers with a CIK (9 members without)" in text and "trades for 2 quarters, 2009q1 to 2026q2" in text
    assert "9100" in text and "2300" in text
    assert "20 d (primary)" in text and "-1.23%, t -1.10" in text and "insider trades through 2026-06-30" in text
    assert "The Form 4 and 8-K fetch is queued" in text and "The event study is queued" in text

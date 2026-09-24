import json
import subprocess

from oracle.gui.app import STATIC_DIR
from tests.gui.test_backtest_js import NODE, PRELUDE, pytestmark  # noqa: F401  (skips without node)

STUDY = {"id": 1, "state": "paused", "paused_reason": None, "created_at": "2026-09-24T12:00:00+00:00",
         "first_day": "2026-06-01", "last_day": "2026-08-03",
         "listings": [{"symbol": "AAPL", "name": "Apple", "region": "US", "sector": "Information Technology",
                       "market_cap": "Mega Cap"},
                      {"symbol": "7203.T", "name": "Toyota", "region": "Japan", "sector": "Consumer Discretionary",
                       "market_cap": "Large Cap"}],
         "total": 6, "done": 2, "pending": 3, "retrying": 1, "running": [], "complete": False,
         "failed": [{"id": 4, "symbol": "7203.T", "as_of": "2026-07-01", "error": "model down", "attempts": 2}],
         "top_failures": [["model down", 2]], "mean_replay_s": 150, "cutoff": None, "waiting": None,
         "polled_at": "2026-09-24T12:00:05+00:00", "finished": False, "untouched": False}


def group(name, n, hit, rel, ret, vs, conf):
    return {"name": name, "n": n, "hit_rate": hit, "hit_rate_vs_basket": rel, "mean_return": ret,
            "mean_vs_basket": vs, "mean_confidence": conf}


BLANK = [group(name, 0, None, None, None, None, None) for name in ("under 0.60", "0.60 to 0.80")]
REPORT = {
    "study_id": 1, "total": 6, "to_run": 3, "failed": 1, "no_verdict": 0, "verdicts": 2,
    "mix": {"buy": 2, "hold": 0, "sell": 0, "buy_share": 1.0, "mean_confidence": 0.85},
    "horizons": [
        {"horizon": "1m", "months": 1, "scored": 2, "pending": 0, "unpriced": 0, "hit_rate": 0.5,
         "hit_ci": [0.24, 0.76], "hit_rate_vs_basket": 1.0, "hit_vs_basket_ci": None, "base_rate_up": 0.4,
         "base_rate_beat_basket": 0.5, "expected_hit_rate": 0.45, "expected_hit_rate_vs_basket": 0.5,
         "by_verdict": [group("buy", 2, 0.5, 1.0, 0.012, 0.03, 0.85)],
         "by_band": [*BLANK, group("0.80 and up", 2, 0.5, 1.0, 0.012, 0.03, 0.85)]},
        {"horizon": "3m", "months": 3, "scored": 0, "pending": 2, "unpriced": 0, "hit_rate": None,
         "hit_rate_vs_basket": None, "by_verdict": [], "by_band": []}],
    "refetch_listings": ["7203.T"], "prices_checked_at": "2026-09-24T06:00:00+00:00", "price_failures": ["AAPL"],
    "limits": ["The listings and their fundamentals are today's (survivorship)."],
}


def run(body: str, study=STUDY, cutoff=None, report=None):
    script = f"""
        {PRELUDE}
        ANSWERS = {{ "GET /api/replay": {{ study: {json.dumps(study)}, cutoff: {json.dumps(cutoff)} }},
                     "GET /api/replay/report": {{ report: {json.dumps(report)} }},
          "POST /api/replay": {{ id: 3, kind: "replay", state: "done", result: {{ left_out: ["UK Energy"] }} }},
          "POST /api/replay/replace": {{ id: 4, kind: "replay", state: "done", result: {{}} }} }};
        const sent = [];
        const plain = globalThis.fetch;
        globalThis.fetch = async (url, options) => {{
          if (options?.body) sent.push([url, JSON.parse(options.body)]);
          return plain(url, options);
        }};
        const {{ replaySection }} = await import({json.dumps((STATIC_DIR / "lib" / "replay.js").as_uri())});
        const section = replaySection();
        const node = section.node;
        section.start(); await settle();
        {body}
        process.exit(0);
    """
    out = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def test_without_a_study_the_card_offers_to_create_one_paused():
    shown = run("""
        labelled(node, "First replay day").value = "2026-06-01";
        asked.length = 0;
        button(node, "Create study").fire("click"); await settle();
        labelled(node, "First replay day").value = "2026-06-01";  // the card reloaded after the job
        labelled(node, "Replay listings").value = " AAPL, 7203.T ,";
        button(node, "Create study").fire("click"); await settle();
        console.log(JSON.stringify([asked, sent, node.textContent]));
    """, study=None)
    asked, sent, text = shown
    assert asked[0] == "POST /api/replay"
    assert sent == [["/api/replay", {"first": "2026-06-01", "symbols": []}],
                    ["/api/replay", {"first": "2026-06-01", "symbols": ["AAPL", "7203.T"]}]]
    assert "no model cutoff is measured yet" in text and "press Start" in text
    assert "Left out, nothing fits: UK Energy." in text


def test_a_measured_cutoff_fills_the_first_day():
    value = run("""console.log(JSON.stringify(labelled(node, "First replay day").value));""", study=None,
                cutoff="2025-07-01")
    assert value == "2025-07-01"


def test_a_paused_study_shows_progress_listings_and_failures_with_start():
    text = run("console.log(JSON.stringify(node.textContent));")
    assert "2 done, 3 queued, 1 failed of 6 (2026-06-01 to 2026-08-03); 3 min per replay on average." in text
    assert "Listings: AAPL (US, Information Technology), 7203.T (Japan, Consumer Discretionary)." in text
    assert "Nothing runs until you press Start" in text and "2 x model down" in text
    assert "1 cell waiting for its one automatic retry" in text and "Last checked 2026-09-24 12:00 UTC." in text


def test_before_the_first_replay_a_listing_can_be_replaced():
    shown = run("""
        labelled(node, "Replace 7203.T with").value = " 6758.T ";
        node.all((n) => n.tag === "button" && n.textContent === "Replace")[1].fire("click"); await settle();
        console.log(JSON.stringify(sent));
    """, study={**STUDY, "untouched": True})
    assert shown == [["/api/replay/replace", {"old": "7203.T", "new": "6758.T"}]]


def test_a_finished_study_with_failures_says_so():
    done = {**STUDY, "state": "running", "pending": 0, "done": 5, "finished": True}
    assert "Finished, with failed replays" in run("console.log(JSON.stringify(node.textContent));", study=done)


def test_start_pause_and_retry_buttons_send_their_requests():
    shown = run("""
        asked.length = 0;
        button(node, "Start").fire("click"); await settle();
        button(node, "Retry failed (1)").fire("click"); await settle();
        button(node, "Retry").fire("click"); await settle();
        console.log(JSON.stringify([asked.filter((a) => a.startsWith("POST")), sent]));
    """)
    posts, sent = shown
    assert posts == ["POST /api/replay/start", "POST /api/replay/retry", "POST /api/replay/retry"]
    assert sent[1:] == [["/api/replay/retry", {}], ["/api/replay/retry", {"cell_id": 4}]]


def test_a_running_study_shows_the_current_replay_or_why_it_waits():
    running = {**STUDY, "state": "running", "running": [{"id": 3, "symbol": "AAPL", "as_of": "2026-07-01",
                                                          "job_id": 9, "age_s": 125}]}
    text = run("console.log(JSON.stringify(node.textContent));", study=running)
    assert "Replaying AAPL as of 2026-07-01, 2 min so far." in text and "Pause" in text
    waiting = {**STUDY, "state": "running", "waiting": "a tick is due at 22:30"}
    assert "Waiting: a tick is due at 22:30." in run("console.log(JSON.stringify(node.textContent));", study=waiting)


def test_a_paused_study_says_why():
    paused = {**STUDY, "paused_reason": "paused after 3 replays failed in a row"}
    assert "Paused: paused after 3 replays failed in a row" in run("console.log(JSON.stringify(node.textContent));",
                                                                  study=paused)


def test_a_refusal_shows_on_the_card():
    text = run("""
        ANSWERS["POST /api/replay/start"] = { status: 400, error: "no replay study yet" };
        button(node, "Start").fire("click"); await settle();
        console.log(JSON.stringify(node.textContent));
    """)
    assert "no replay study yet" in text


def test_the_score_shows_headline_numbers_a_table_and_the_limits():
    text = run("console.log(JSON.stringify(node.textContent));", report=REPORT)
    assert "2 verdicts, 0 without a verdict, 1 failed, 3 still to run, of 6 replays." in text
    assert "Verdict mix: 2 buy, 0 hold, 0 sell (100% buys), mean confidence 0.85." in text
    assert ("1 month: 2 scored, 0 pending, 0 without prices; hit rate 50% (95% 24% to 76%), 100% against the "
            "basket. No skill: of the called windows 40% rose and 50% beat the basket, so this mix of calls would hit "
            "45% (50% against the basket) by chance.") in text
    assert "Study prices last checked 2026-09-24 06:00 UTC. Failed then: AAPL." in text and "mixes currencies" in text
    assert "3 months: 0 scored, 2 pending" in text
    assert "buy250%100%+1.20%+3.00%0.85" in text and "0.80 and up2" in text
    assert "A window closed since 7203.T last had prices fetched" in text and "(survivorship)" in text


def test_the_report_is_fetched_again_only_when_the_status_score_moves():
    shown = run("""
        button(node, "Start").fire("click"); await settle();
        const once = asked.filter((a) => a.startsWith("GET /api/replay/report")).length;
        ANSWERS["GET /api/replay"].study = { ...ANSWERS["GET /api/replay"].study, score: { verdicts: 3 } };
        button(node, "Start").fire("click"); await settle();
        console.log(JSON.stringify([once, asked.filter((a) => a.startsWith("GET /api/replay/report")).length]));
    """, report=REPORT)
    assert shown == [1, 2]


def test_update_prices_runs_the_job_and_reloads_the_score():
    shown = run("""
        ANSWERS["POST /api/replay/prices"] = { id: 3, state: "done", result: { listings: [
          { symbol: "7203.T", added: 40, source: "yahoo", error: null },
          { symbol: "AAPL", added: 0, source: null, error: "no price data" }] } };
        asked.length = 0;
        button(node, "Update prices").fire("click"); await settle(); await settle();
        console.log(JSON.stringify([asked, node.textContent]));
    """, report=REPORT)
    asked, text = shown
    assert asked[:3] == ["POST /api/replay/prices", "GET /api/replay", "GET /api/replay/report"]
    assert "Prices fetched for 1 of 2 listings; failed: AAPL (no price data)." in text

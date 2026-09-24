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


def run(body: str, study=STUDY, cutoff=None):
    script = f"""
        {PRELUDE}
        ANSWERS = {{ "GET /api/replay": {{ study: {json.dumps(study)}, cutoff: {json.dumps(cutoff)} }},
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

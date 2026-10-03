from __future__ import annotations

import json
import sqlite3

from oracle.store.migrations import run_ddl

# lab_tests: every pre-registered test (research-lab 01). horizons is a JSON list of trading days
# ([] for a strategy, which counts once toward N). bar is the t the verdict needs, frozen at
# registration (NULL for a strategy, judged by bar_rule); bar_inclusive: t at the bar passes. A data
# run is refused until commit_hash is set. legacy rows are the tests registered before the lab,
# back-filled below with the rules they were judged by; result_key is where their kept result is.
DDL = """
CREATE TABLE lab_tests (
    key               TEXT PRIMARY KEY,
    label             TEXT    NOT NULL,
    kind              TEXT    NOT NULL CHECK (kind IN ('signal', 'event', 'strategy')),
    registered_on     TEXT    NOT NULL,
    horizons          TEXT    NOT NULL,
    rule              TEXT    NOT NULL,
    universe          TEXT    NOT NULL,
    expected_sign     TEXT    NOT NULL,
    expected_size     TEXT    NOT NULL,
    source            TEXT    NOT NULL,
    power             TEXT    NOT NULL,
    costs             TEXT    NOT NULL,
    bar               REAL,
    bar_inclusive     INTEGER NOT NULL,
    bar_rule          TEXT    NOT NULL,
    kill_stage        TEXT    NOT NULL,
    parent            TEXT,
    different_because TEXT,
    commit_hash       TEXT,
    legacy            INTEGER NOT NULL,
    result_key        TEXT    NOT NULL,
    note              TEXT    NOT NULL DEFAULT ''
) WITHOUT ROWID;
"""

SP500 = "S&P 500 point-in-time members from 2010 (research testbed); train 2010-2019 reported, confirm 2020+ judged"
UNSTATED = "not stated (registered before the lab template)"
T2 = "confirm IC > 0, t > 2, positive in more than half the counted segments (next-signals 02)"
EVENT_T2 = "confirm mean CAR at 20 d with t above 2 in the train mean's sign (next-signals 05)"
SIGNAL_DOC = "DECISIONS 2026-10-01 Signal test (next-signals 02)"
FUND_DOC = "DECISIONS 2026-10-01 Fundamental signals (next-signals 04), corrected 44fafb9"
INSIDER_DOC = "DECISIONS 2026-10-01 Insider buys and typed-event studies (next-signals 05), corrected cf4445d"
COSTS = "none (a signal IC; no turnover or break-even cost reported then)"
KILL = "confirm verdict"


def _signal(key, label, h, rule, sign, commit, doc, on="2026-10-01"):
    return dict(key=key, label=label, kind="signal", registered_on=on, horizons=[h], rule=f"{rule} ({doc})",
                universe=SP500, expected_sign=sign, expected_size=UNSTATED, source=doc, power=UNSTATED, costs=COSTS,
                bar=2.0, bar_rule=T2, kill_stage=KILL, commit_hash=commit, result_key=key)


def _event(item, label, rule):
    return dict(key=f"event:{item}", label=label, kind="event", registered_on="2026-10-01", horizons=[20],
                rule=f"{rule}; CAR vs sector from the next session's open, reported at 1, 5, 20, 60 d ({INSIDER_DOC})",
                universe=SP500, expected_sign="train decides the sign", expected_size=UNSTATED, source=INSIDER_DOC,
                power=UNSTATED, costs=COSTS, bar=2.0, bar_rule=EVENT_T2, kill_stage=KILL,
                commit_hash="6bff0c11c090471d65c87faae80a4f0a879ddb19", result_key=f"event_study:{item}")


TRACKED = "DECISIONS 2026-10-01 Forward-only trackers for the near misses"
TRACKER_NOTE = "forward only: decision days after 2026-10-01, judged on 2027-10-01"


def _tracker(row):
    return {**row, "key": f"forward:{row['key'].removeprefix('event:')}", "label": f"{row['label']} (forward)",
            "registered_on": "2026-10-01", "commit_hash": "aa016db9c5b6ba9a284f8c652aa9f89d9c04be60",
            "source": TRACKED, "rule": f"As registered, scored only after 2026-10-01 ({TRACKED})",
            "result_key": f"forward:{row['result_key']}", "note": TRACKER_NOTE}


MOMENTUM = _signal("momentum_12_1", "12-1 momentum", 20,
                   "the year to the last close leaving out its last month, scored against 20-day sector-excess return",
                   "positive", "2abe9c64aa66ea2060ffec79e988a09b9096ade7", SIGNAL_DOC)
REVERSAL = _signal("reversal_1m", "1-month reversal", 5, "minus the last 21 trading days' return",
                   "positive", "2abe9c64aa66ea2060ffec79e988a09b9096ade7", SIGNAL_DOC)
SUE = _signal("sue", "Standardised unexpected earnings", 60,
              "quarterly EPS change on a year before over the spread of the up to 8 changes before it",
              "positive", "10b351225b19afd0f0637440ced5730fd38675b4", FUND_DOC)
GP = _signal("gross_profitability", "Gross profitability", 20, "trailing-year gross profit over total assets",
             "positive", "10b351225b19afd0f0637440ced5730fd38675b4", FUND_DOC)
EY = _signal("earnings_yield", "Earnings yield", 20, "trailing-year EPS over the last close",
             "positive", "10b351225b19afd0f0637440ced5730fd38675b4", FUND_DOC)
INSIDER = {**_signal("insider_buys", "Insider buys", 20,
                     "distinct officers and directors with an open-market purchase in Form 4s known in the 90 days "
                     "before; PASS only when both horizons pass", "positive",
                     "6bff0c11c090471d65c87faae80a4f0a879ddb19", INSIDER_DOC), "horizons": [20, 60]}
E202 = _event("2.02", "8-K 2.02 results", "8-K item 2.02 (results of operations), original filings")
E101 = _event("1.01", "8-K 1.01 material agreement", "8-K item 1.01 (material definitive agreement)")
E201 = _event("2.01", "8-K 2.01 acquisition or disposal", "8-K item 2.01 (acquisition or disposal of assets)")
CLUSTER = _event("insider_cluster", "Insider-buy cluster",
                 "a day with 3 or more insiders buying in the 30 days to it, at most one per company in 30 days")
LAYA_DOC = "DECISIONS 2026-09-30 Laya predictive test, amended 2026-09-30, 2026-10-01 (twice)"
LAYA = dict(key="laya", label="Laya news score", kind="signal", registered_on="2026-09-30", horizons=[5, 20],
            rule=f"Laya's per-article direction x probability by relation (own, competitor, industry), combined by a "
                 f"trailing purged 730-day IC fit, against industry-excess open-to-open returns; useful only if it "
                 f"also beats the attention and momentum baselines ({LAYA_DOC})",
            universe="the main watchlist (51 companies) from 2022-09-21, survivorship: today's list",
            expected_sign="positive", expected_size=UNSTATED, source=LAYA_DOC, power=UNSTATED, costs=COSTS, bar=2.0,
            bar_rule="combined score IC > 0, t > 2, positive in more than half the counted segments, on both "
                     "horizons (laya-industry 06)", kill_stage=KILL,
            commit_hash="058043259696fa9d80b0cb7a83f8cdfee68fc0fb", result_key="laya:main",
            note="the main run decides; the own-news and sensitivity runs are looks")
TREND_DOC = "DECISIONS 2026-10-01 Trend overlay: the one rule, clarified 2026-10-02 (7960873)"
TREND = dict(key="trend_overlay", label="Trend overlay (10-month SMA)", kind="strategy", registered_on="2026-10-01",
             horizons=[], rule=f"Hold ^SP500TR at a month end at or above its 10-month moving average, else cash from "
                               f"the next open ({TREND_DOC})",
             universe="S&P 500 total return (^SP500TR); choose 1988-2007 reported, confirm 2008+ judged",
             expected_sign="smaller falls", expected_size=UNSTATED, source=TREND_DOC, power=UNSTATED,
             costs="0.1% of the value per switch; no dealing or platform fee", bar=None,
             bar_rule="worst fall at most two thirds of buy and hold's and CAGR at most 1 point below it "
                      "(boring-plan 04)", kill_stage=KILL, commit_hash="aa016db9c5b6ba9a284f8c652aa9f89d9c04be60",
             result_key="trend_overlay")
LEGACY = (MOMENTUM, REVERSAL, SUE, GP, EY, INSIDER, E202, E101, E201, CLUSTER, LAYA, TREND,
          *map(_tracker, (MOMENTUM, REVERSAL, SUE, EY, {**E101, "expected_sign": "negative"})))

COLUMNS = ("key", "label", "kind", "registered_on", "horizons", "rule", "universe", "expected_sign", "expected_size",
           "source", "power", "costs", "bar", "bar_inclusive", "bar_rule", "kill_stage", "parent", "different_because",
           "commit_hash", "legacy", "result_key", "note")


def up(conn: sqlite3.Connection) -> None:
    run_ddl(conn, DDL)
    for row in LEGACY:
        values = {"parent": None, "different_because": None, "note": "", **row, "horizons": json.dumps(row["horizons"]),
                  "bar_inclusive": 0, "legacy": 1}
        conn.execute(f"INSERT INTO lab_tests ({', '.join(COLUMNS)}) VALUES ({', '.join('?' * len(COLUMNS))})",
                     [values[c] for c in COLUMNS])
